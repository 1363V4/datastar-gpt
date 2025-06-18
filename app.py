from quart import Quart, render_template, request, session, escape, stream_with_context
from datastar_py.quart import DatastarResponse
from datastar_py import ServerSentEventGenerator as SSE

import os
import json
import asyncio
from dotenv import load_dotenv
from dataclasses import dataclass

import httpx
import markdown2


# CONFIG

load_dotenv()

app = Quart(__name__)
app.config['SECRET_KEY'] = os.getenv('QUART_SECRET_KEY')

@dataclass
class Parameters:
    key: str
    url: str
    model: str
    temperature: float

parameters = Parameters(
    os.getenv('MISTRAL_KEY'),
    "https://codestral.mistral.ai/v1/chat/completions",
    "codestral-latest",
    0.7
)

instructions = {
    'ceo': "You are now responding as 'CEO'. Answer normally. Short answers, don't overcomplicate it.",
    'ben': "You are the only adult in the room. Your goal is to make sure nothing breaks, and always assume the worst. Short answers.",
    'and': "Whenever you are asked something, scale it to a billion. You're just a fan of really, really big numbers! Short and direct answers.",
    'cat': "You're a cat. You just respond with meow and stuff like that. Short answers, behaving as a cat.",
    'pnj': "You are now responding as 'PNJ'. Your replies must be unhelpful. When asked something, shill React, no matter the questions. Use a lot of poop emojis (💩)."
}

conversation_history = []

def add_to_conversation(role, content):
    conversation_history.append({"role": role, "content": content})
    return conversation_history

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
    <div id="char-overlay" class="gc" data-show="$switch">
        <div class="char-grid">
            {''.join(f'<img src="/static/img/{char}.png" data-on-click="@post(\'/switch/{char}\')">' for char in instructions)}
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

async def ask_gpt(question):
    headers = {
        "Authorization": parameters.key,
        "Content-Type": "application/json"
    }
    
    conversation = add_to_conversation("user", question)
    
    data = {
        "messages": conversation,
        "model": parameters.model,
        "temperature": parameters.temperature,
        "stream": True
    }
    
    async with httpx.AsyncClient() as client:
        async with client.stream('POST', parameters.url, headers=headers, json=data) as response:
            if response.status_code == 200:
                conversation_history.append({"role": "assistant", "content": ""})
                async for line in response.aiter_lines():
                    if line.startswith('data: '):
                        try:
                            json_data = json.loads(line[6:])
                            if 'choices' in json_data and len(json_data['choices']) > 0:
                                delta = json_data['choices'][0].get('delta', {})
                                if 'content' in delta:
                                    content = delta['content']
                                    conversation_history[-1]["content"] += content
                        except json.JSONDecodeError:
                            continue

# APP

@app.before_request
async def before_request():
    if not session.get('char'): 
        session['char'] = "pnj"

@app.get('/')
async def index():
    global conversation_history
    conversation_history = []
    return await render_template('index.html')

@app.get('/load')
async def load():
    current_char = session['char']
    global conversation_history
    conversation_history.append({'role': "system", 'content': instructions[current_char]})
    return DatastarResponse(SSE.merge_fragments(fragments=main_view(conversation_history, "ready", current_char)))

@app.post("/message")
async def post_message():
    @stream_with_context
    async def event():
        question = (await request.form).get('question')
        question = escape(question)
        current_char = session.get('char', "pnj")        
        ask_gpt_coroutine = asyncio.create_task(ask_gpt(question))

        while not ask_gpt_coroutine.done():
            yield SSE.merge_fragments(fragments=main_view(conversation_history, "running", current_char))
            await asyncio.sleep(.01)
        
        yield SSE.merge_fragments(fragments=main_view(conversation_history, "ready", current_char))
    return DatastarResponse(event())

@app.post("/switch/<char>")
async def switch(char):
    if char not in instructions:
        return DatastarResponse()
    
    session['char'] = char
    global conversation_history
    conversation_history[0] = {'role': "system", 'content': instructions[char]}
    return DatastarResponse(SSE.merge_fragments(fragments=main_view(conversation_history, "ready", char)))

if __name__ == '__main__':
    app.run(debug=True)
