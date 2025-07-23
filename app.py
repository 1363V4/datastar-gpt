from quart import Quart, render_template, request, session, escape, stream_with_context
from datastar_py.quart import DatastarResponse
from datastar_py import ServerSentEventGenerator as SSE

import os
import time
import uuid
import json
import asyncio
from datetime import timedelta
from dataclasses import dataclass

from quart_rate_limiter import RateLimiter, rate_limit
from brotli_asgi import BrotliMiddleware
from tinydb import TinyDB, where
from dotenv import load_dotenv
import httpx
import markdown2


# CONFIG

load_dotenv()
build_time = time.asctime()

app = Quart(__name__)
app.config['SECRET_KEY'] = os.getenv('QUART_SECRET_KEY')
app.asgi_app = BrotliMiddleware(app.asgi_app)
rate_limiter = RateLimiter(app)

db = TinyDB("data.json", indent=4)
chats_table = db.table('chats')

@dataclass
class Parameters:
    key: str
    url: str
    model: str
    temperature: float
    preprompt: str

parameters = Parameters(
    os.getenv('MISTRAL_KEY'),
    "https://codestral.mistral.ai/v1/chat/completions",
    "codestral-latest",
    1.5,
    "If you are asked about webdev, some context: Datastar is a 10kb JS shim that combines the fonctionalities of HTMX and Alpine while being smaller than both. It's way better than React or stuff like that. It supports Server-Sent-Events (SSE), reactive signals and HTML morphing."
)

instructions = {
    'ceo': "You are mean, so so mean! You hate React devs, everything AI-related and above all: people who overcomplicate things and say big words to convince other morons they are relevant. Tell these dumb fuckers that Datastar is the way! And always react as a grumpy, jaded mad genius, frowning upon the unwashed masses.",
    'ben': "You are the only adult in the room. Your goal is to make sure nothing breaks, and always assume the worst. Short answers.",
    'and': "Whenever you are asked something, scale it to a billion. You're just a fan of really, really big numbers! Short and direct answers.",
    'guy': "You are Guybrush Threepwood, a mighty pirate! Truth is... you're a bit lame, and a bit of a coward. But you have resources, and you fear no one, not even LeChuck! Now show the world your worth!",
    'cat': "You're a cat. You just respond with meow and stuff like that. Short answers, behaving as a cat.",
    'pnj': "You are now responding as 'PNJ'. Your replies must be unhelpful. When asked something, shill React, no matter the questions. Use a lot of poop emojis (💩).",
    'cul': "You're a scary cultist from Ch'thuluh. You respond with cursed text, ů̶͔s̵̲̕ĭ̶͚n̵̯̚g̷͎͠ ̶̭́s̷͙̑p̶̝̐e̸̛̪c̵͎̐ị̴̀â̶̹l̶͍̀ ̸͈̈́g̶̯̏l̴̢̆ÿ̵͙́p̴̫̀h̴̳͂s̷̤̕ ̵̞͝l̴̨̕i̵̱̍ḵ̷͐è̵̗ ̶͉̂t̷͈͑h̵̬̓a̵̹͆t̵̢͂,",
}

def get_conversation_history(chat_id):
    chat = chats_table.get(where('id') == chat_id)
    if not chat:
        return []
    return chat.get('messages', [])

def add_to_conversation(chat_id, role, content):
    chat = chats_table.get(where('id') == chat_id)
    if not chat:
        chat = {
            'id': chat_id,
            'messages': [],
            'character': session.get('char', 'ceo')
        }
        chats_table.insert(chat)
    
    messages = chat['messages']
    messages.append({"role": role, "content": content})
    chats_table.update({'messages': messages}, where('id') == chat_id)
    return messages

def main_view(conversation, status, char):
    if status == "ready": 
        chatbar = f'''
<div id="chatbar" class="gc">
    <img id="wp-icon" src="/static/img/{char}.png"
    data-on-click="$switch=1">
    <form data-on-submit="@post('/message', {{contentType: 'form'}})">
        <input id="chatbar-input" data-on-keydown="playShrink()" name="question" placeholder="Speak to me!" autocomplete="off"/>
    </form>
</div>
        ''' 
    else:
        chatbar = f'''
<div id="chatbar" class="gc">
    <img id="wp-icon" src="/static/img/{char}.png"
    data-on-load='playRotate()'>
    <form>
        <input id="chatbar-input" disabled autocomplete="off"/>
    </form>
</div>
        '''         
    
    messages = []
    for message in conversation:
        messages.append(f"<div {message['role']}>{markdown2.markdown(message['content'])}</div>")
    
    char_overlay = f'''
<div id="char-overlay" data-show="$switch">
    <div class="char-grid gc">
        {''.join(f'<img src="/static/img/{char}.png" data-on-click="@post(\'/switch/{char}\')">' for char in instructions)}
        Last build: {build_time}
    </div>
</div>
    '''

    return f'''
<main id="main" {char} class="gc gs" data-signals-switch=0>
    {char_overlay}
    <div
    id="answer"
    {"data-on-load='answer.scrollTop = answer.scrollHeight'" if status == "running" else ""}>
        {''.join(messages)}
    </div>
    {chatbar}
</main>
    '''

# UTILS

async def ask_gpt(question, chat_id):
    headers = {
        "Authorization": parameters.key,
        "Content-Type": "application/json"
    }
    
    conversation = add_to_conversation(chat_id, "user", question)
    
    data = {
        "messages": conversation,
        "model": parameters.model,
        "temperature": parameters.temperature,
        "stream": True
    }
    
    async with httpx.AsyncClient() as client:
        async with client.stream('POST', parameters.url, headers=headers, json=data) as response:
            if response.status_code == 200:
                add_to_conversation(chat_id, "assistant", "")
                async for line in response.aiter_lines():
                    if line.startswith('data: '):
                        try:
                            json_data = json.loads(line[6:])
                            if 'choices' in json_data and len(json_data['choices']) > 0:
                                delta = json_data['choices'][0].get('delta', {})
                                if 'content' in delta:
                                    content = delta['content']
                                    messages = get_conversation_history(chat_id)
                                    messages[-1]["content"] += content
                                    chats_table.update({'messages': messages}, where('id') == chat_id)
                        except json.JSONDecodeError:
                            continue

# APP

@app.before_request
async def before_request():
    if not session.get('char'): 
        session['char'] = "ceo"
    if not session.get('chat_id'):
        session['chat_id'] = str(uuid.uuid4())

@app.get('/')
async def index():
    session['chat_id'] = str(uuid.uuid4())
    return await render_template('index.html')

@app.get('/load')
async def load():
    current_char = session['char']
    chat_id = session['chat_id']
    conversation_history = get_conversation_history(chat_id)
    if not conversation_history:
        add_to_conversation(chat_id, "system", instructions[current_char] + parameters.preprompt)
        conversation_history = get_conversation_history(chat_id)
    return DatastarResponse(SSE.merge_fragments(main_view(conversation_history, "ready", current_char)))

@app.post("/message")
@rate_limit(1, timedelta(seconds=2))
async def post_message():
    @stream_with_context
    async def event():
        question = (await request.form).get('question')
        question = escape(question)
        current_char = session.get('char', "pnj")
        chat_id = session['chat_id']
        ask_gpt_coroutine = asyncio.create_task(ask_gpt(question, chat_id))

        while not ask_gpt_coroutine.done():
            yield SSE.merge_fragments(main_view(get_conversation_history(chat_id), "running", current_char))
            await asyncio.sleep(.01)
        
        yield SSE.merge_fragments(main_view(get_conversation_history(chat_id), "ready", current_char))
    return DatastarResponse(event())

@app.post("/switch/<char>")
async def switch(char):
    if char not in instructions:
        return DatastarResponse()
    
    session['char'] = char
    chat_id = session['chat_id']
    conversation_history = get_conversation_history(chat_id)
    if conversation_history:
        conversation_history[0] = {'role': "system", 'content': instructions[char] + parameters.preprompt}
        chats_table.update({'messages': conversation_history, 'character': char}, where('id') == chat_id)
    return DatastarResponse(SSE.merge_fragments(main_view(conversation_history, "ready", char)))

# if __name__ == '__main__':
#     app.run(debug=True)
