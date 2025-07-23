from quart import Quart, render_template, request, escape, stream_with_context
from datastar_py.quart import make_datastar_response
from datastar_py.sse import ServerSentEventGenerator as SSE

import os
import json
import uuid
import asyncio
from dotenv import load_dotenv
from dataclasses import dataclass

import markdown2
import httpx


# CONFIG

load_dotenv()

app = Quart(__name__)
app.config['SECRET_KEY'] = os.getenv('QUART_SECRET_KEY')

@dataclass
class Parameters:
    key: str
    url: str
    model: str
    instructions: str
    temperature: float

parameters = Parameters(
    os.getenv('MISTRAL_KEY'),
    "https://codestral.mistral.ai/v1/chat/completions",
    "codestral-latest",
    "Réponds simplement",
    # "On joue un jeu où tu es la pire wedding planneuse de tous les temps. Tu réponds toujours à côté de la plaque, et encore, dans le meilleur des cas ! Tu réponds toujours hors budget, ou à l'inverse total de ce qu'une wedding planneuse expérimentée pourrait dire. Sois le moins professionnelle possible ! IMPORTANT : tu réponds toujours en une phrase.",
    0.7
)

conversation_history = []

def add_to_conversation(role, content):
    conversation_history.append({"role": role, "content": content})
    return conversation_history

# UTILS

def get_response_content(response):
    return response.json()['choices'][0]['message']['content']

async def ask_gpt(question):
    headers = {
        "Authorization": parameters.key,
        "Content-Type": "application/json"
    }
    
    conversation = add_to_conversation("user", parameters.instructions + question)
    
    data = {
        "messages": conversation,
        "model": parameters.model,
        "temperature": parameters.temperature,
        "stream": True
    }
    
    full_response = ""
    async with httpx.AsyncClient() as client:
        async with client.stream('POST', parameters.url, headers=headers, json=data) as response:
            if response.status_code == 200:
                async for line in response.aiter_lines():
                    if line.startswith('data: '):
                        try:
                            json_data = json.loads(line[6:])
                            if 'choices' in json_data and len(json_data['choices']) > 0:
                                delta = json_data['choices'][0].get('delta', {})
                                if 'content' in delta:
                                    content = delta['content']
                                    full_response += content
                                    yield content
                        except json.JSONDecodeError:
                            continue
                
                add_to_conversation("assistant", full_response)
            else:
                yield f"There's a problem in da llm"

# APP

@app.route('/')
async def index():
    global conversation_history
    conversation_history = []
    return await render_template('index.html')

@app.post("/message")
async def post_message():
    @stream_with_context
    async def event():
        question = (await request.form).get('question')
        question = escape(question)
        yield SSE.merge_fragments(fragments=[f"<div user>{question}</div>"], selector="#answer", merge_mode="append")
        yield SSE.merge_fragments(fragments=['''<input id="chatbar-input" disabled/>'''])
        yield SSE.execute_script(script="playRotate()")
        
        response_id = uuid.uuid4().int
        yield SSE.merge_fragments(fragments=[f"<div ia id='id{response_id}'></div>"], selector="#answer", merge_mode="append")
        await asyncio.sleep(1)
        full_text = ""
        async for chunk in ask_gpt(question):
            full_text += chunk
            formatted_text = markdown2.markdown(full_text)
            yield SSE.merge_fragments(fragments=[f"<div ia id='id{response_id}'>{formatted_text}</div>"])
        
        yield SSE.merge_fragments(fragments=['''<input id="chatbar-input" data-on-keydown="playShrink()" name="question" placeholder="Parlez-moi !"/>'''])
    return await make_datastar_response(event())

if __name__ == '__main__':
    app.run(debug=True)