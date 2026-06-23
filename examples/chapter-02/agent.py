"""Chapter 2 reference: an Agent Loop with one read-only tool."""

from collections.abc import Callable, Iterable
import os
from pathlib import Path
from typing import Any

import anthropic

from tools import execute_tool, tool_definitions


DEFAULT_BASE_URL = "https://api.deepseek.com/anthropic"
DEFAULT_MODEL = "claude-sonnet-4.6"
SYSTEM_PROMPT = "You are a helpful coding assistant."
SCRIPT_DIR = Path(__file__).resolve().parent


def create_client() -> anthropic.Anthropic:
    """Create an Anthropic client configured for DeepSeek by default."""
    api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Set DEEPSEEK_API_KEY or ANTHROPIC_API_KEY before running agent.py."
        )

    base_url = os.getenv("ANTHROPIC_BASE_URL", DEFAULT_BASE_URL)
    return anthropic.Anthropic(base_url=base_url, api_key=api_key)


def assistant_content(blocks: Iterable[Any]) -> list[dict[str, Any]]:
    """Keep text and tool calls while omitting thinking blocks."""
    content: list[dict[str, Any]] = []

    for block in blocks:
        if block.type == "text":
            content.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            content.append(
                {
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                }
            )

    return content


def execute_tool_calls(blocks: Iterable[Any]) -> list[dict[str, Any]]:
    """Execute every tool request and pair each result with its call ID."""
    results: list[dict[str, Any]] = []

    for block in blocks:
        if block.type != "tool_use":
            continue

        result = execute_tool(block.name, block.input)
        results.append(
            {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result,
            }
        )

    return results


def run_agent_turn(
    client: anthropic.Anthropic,
    messages: list[dict[str, Any]],
    *,
    model: str = DEFAULT_MODEL,
    output_fn: Callable[[str], None] = print,
) -> None:
    """Keep calling the model until it returns no more tool requests."""
    while True:
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=messages,
            tools=tool_definitions,
        )

        content = assistant_content(response.content)
        for block in content:
            if block["type"] == "text":
                output_fn(block["text"])

        messages.append({"role": "assistant", "content": content})
        tool_results = execute_tool_calls(response.content)

        if not tool_results:
            return

        messages.append({"role": "user", "content": tool_results})


def run_chat(
    client: anthropic.Anthropic,
    *,
    model: str = DEFAULT_MODEL,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> None:
    """Run the outer user-input loop around the inner tool loop."""
    messages: list[dict[str, Any]] = []

    while True:
        user_input = input_fn("> ")
        if user_input.strip().lower() == "exit":
            return
        if not user_input.strip():
            continue

        messages.append({"role": "user", "content": user_input})
        run_agent_turn(
            client,
            messages,
            model=model,
            output_fn=output_fn,
        )


def main() -> None:
    os.chdir(SCRIPT_DIR)
    run_chat(create_client())


if __name__ == "__main__":
    main()
