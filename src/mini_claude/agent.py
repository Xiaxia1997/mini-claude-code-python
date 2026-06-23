import os

import anthropic


ANTHROPIC_BASE_URL = "https://api.deepseek.com/anthropic"
DEEPSEEK_API_KEY = os.environ["DEEPSEEK_API_KEY"]

client = anthropic.Anthropic(
    base_url=ANTHROPIC_BASE_URL,
    api_key=DEEPSEEK_API_KEY,
)

context = []

while True:
    user_input = input("> ")
    if user_input == "exit":
        break

    context.append(
        {
            "role": "user",
            "content": [{"type": "text", "text": user_input}],
        }
    )
    response = client.messages.create(
        model="claude-sonnet-4.6",
        max_tokens=4096,
        system="You are a helpful assistant",
        messages=context,
    )

    content_to_save = []
    for block in response.content:
        if block.type == "text":
            print(block.text)
            content_to_save.append({"type": "text", "text": block.text})

    context.append({"role": "assistant", "content": content_to_save})
