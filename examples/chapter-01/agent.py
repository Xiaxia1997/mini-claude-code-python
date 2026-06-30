"""Chapter 1 reference: a minimal multi-turn LLM client."""

from collections.abc import Callable, Iterable
import os
from typing import Any

import anthropic


DEFAULT_BASE_URL = "https://api.deepseek.com/anthropic"
DEFAULT_MODEL = "claude-sonnet-4.6"
SYSTEM_PROMPT = "You are a helpful assistant."


def create_client() -> anthropic.Anthropic:
    """Create an Anthropic client configured for DeepSeek by default."""
    api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Set DEEPSEEK_API_KEY or ANTHROPIC_API_KEY before running agent.py."
        )

    base_url = os.getenv("ANTHROPIC_BASE_URL", DEFAULT_BASE_URL)
    return anthropic.Anthropic(base_url=base_url, api_key=api_key)


def text_content(blocks: Iterable[Any]) -> list[dict[str, str]]:
    """Convert text blocks into the message format sent back to the API."""
    return [
        {"type": "text", "text": block.text}
        for block in blocks
        if block.type == "text"
    ]


def run_chat(
    client: anthropic.Anthropic,
    *,
    model: str = DEFAULT_MODEL,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> None:
    """Run a synchronous terminal chat until the user enters ``exit``."""
    messages: list[dict[str, Any]] = []

    while True:
        user_input = input_fn("> ")
        if user_input.strip().lower() == "exit":
            return
        if not user_input.strip():
            continue

        messages.append({"role": "user", "content": user_input})
        response = client.messages.create(
            model=model,
            max_tokens=8192,
            system=SYSTEM_PROMPT,
            messages=messages,
        )

        assistant_message = text_content(response.content)
        for block in assistant_message:
            output_fn(block["text"])

        messages.append(
            {
                "role": "assistant",
                "content": assistant_message,
            }
        )


def main() -> None:
    run_chat(create_client())


if __name__ == "__main__":
    main()
