"""Chapter 4 reference: async Agent class, REPL, and session resume."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
import os
from pathlib import Path
import signal
import sys
import time
from typing import Any
import uuid

import anthropic

from prompt import build_system_prompt
from session import get_latest_session_id, load_session, save_session
from tools import execute_tool, tool_definitions
from ui import (
    print_assistant_text,
    print_error,
    print_info,
    print_tool_call,
    print_tool_result,
    print_user_prompt,
    print_welcome,
)


DEFAULT_BASE_URL = "https://api.deepseek.com/anthropic"
DEFAULT_MODEL = "claude-sonnet-4.6"
SCRIPT_DIR = Path(__file__).resolve().parent


def response_usage(response: Any) -> tuple[int, int]:
    """Read usage safely from SDK response-like objects."""
    usage = getattr(response, "usage", None)
    return (
        int(getattr(usage, "input_tokens", 0) or 0),
        int(getattr(usage, "output_tokens", 0) or 0),
    )


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
    """Execute tool requests and return Anthropic-compatible tool_result blocks."""
    results: list[dict[str, Any]] = []

    for block in blocks:
        if block.type != "tool_use":
            continue

        print_tool_call(block.name, block.input)
        result = execute_tool(block.name, block.input)
        print_tool_result(block.name, result)
        results.append(
            {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result,
            }
        )

    return results


class Agent:
    """Hold model client, message history, token counters, and session metadata."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None = None,
        model: str = DEFAULT_MODEL,
    ) -> None:
        self.model = model
        self.client = anthropic.AsyncAnthropic(
            base_url=base_url,
            api_key=api_key,
        )
        self.messages: list[dict[str, Any]] = []
        self.system_prompt = build_system_prompt()
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.session_id = uuid.uuid4().hex[:8]
        self.session_start_time = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(),
        )

    async def chat(self, user_message: str) -> None:
        """Run one user turn, including any tool loop the model requests."""
        self.messages.append(
            {
                "role": "user",
                "content": [{"type": "text", "text": user_message}],
            }
        )

        while True:
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=self.system_prompt,
                messages=self.messages,
                tools=tool_definitions,
            )
            input_tokens, output_tokens = response_usage(response)
            self.total_input_tokens += input_tokens
            self.total_output_tokens += output_tokens

            content = assistant_content(response.content)
            for block in content:
                if block["type"] == "text":
                    print_assistant_text("\n" + block["text"])

            self.messages.append({"role": "assistant", "content": content})
            tool_results = execute_tool_calls(response.content)

            if not tool_results:
                break

            self.messages.append({"role": "user", "content": tool_results})

        self._auto_save()

    def clear_history(self) -> None:
        """Clear conversation history and token counters for the current process."""
        self.messages = []
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        print_info("Conversation cleared.")

    def show_cost(self) -> None:
        """Show rough token usage accumulated in this session."""
        print_info(
            f"Tokens: {self.total_input_tokens} in / "
            f"{self.total_output_tokens} out"
        )

    def restore_session(self, data: dict[str, Any]) -> None:
        """Restore message history and counters from a saved session."""
        messages = data.get("messages")
        if not isinstance(messages, list) or not messages:
            return

        self.messages = messages
        metadata = data.get("metadata", {})
        if isinstance(metadata, dict):
            if isinstance(metadata.get("id"), str):
                self.session_id = metadata["id"]
            if isinstance(metadata.get("startTime"), str):
                self.session_start_time = metadata["startTime"]
            self.total_input_tokens = int(metadata.get("totalInputTokens", 0) or 0)
            self.total_output_tokens = int(metadata.get("totalOutputTokens", 0) or 0)

        print_info(f"Session restored ({len(self.messages)} messages).")

    def _auto_save(self) -> None:
        """Persist the current session without interrupting the chat on failure."""
        try:
            save_session(
                self.session_id,
                {
                    "metadata": {
                        "id": self.session_id,
                        "model": self.model,
                        "startTime": self.session_start_time,
                        "messageCount": len(self.messages),
                        "totalInputTokens": self.total_input_tokens,
                        "totalOutputTokens": self.total_output_tokens,
                    },
                    "messages": self.messages,
                },
            )
        except Exception:
            pass


async def run_repl(agent: Agent) -> None:
    """Run the interactive read-eval-print loop."""
    sigint_count = 0

    def handle_sigint(sig, frame):  # type: ignore[no-untyped-def]
        nonlocal sigint_count
        sigint_count += 1
        if sigint_count >= 2:
            print("\nBye!\n")
            raise SystemExit(0)
        print("\nPress Ctrl+C again to exit.")
        print_user_prompt()

    signal.signal(signal.SIGINT, handle_sigint)
    print_welcome()

    while True:
        print_user_prompt()
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!\n")
            return

        user_input = line.strip()
        sigint_count = 0

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit"}:
            print("\nBye!\n")
            return
        if user_input == "/clear":
            agent.clear_history()
            continue
        if user_input == "/cost":
            agent.show_cost()
            continue

        try:
            await agent.chat(user_input)
        except Exception as exc:
            print_error(exc)


def create_agent() -> Agent:
    """Build an Agent from environment variables."""
    api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Set DEEPSEEK_API_KEY or ANTHROPIC_API_KEY before running agent.py."
        )

    return Agent(
        api_key=api_key,
        base_url=os.getenv("ANTHROPIC_BASE_URL", DEFAULT_BASE_URL),
        model=os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL),
    )


def restore_latest_session(agent: Agent) -> None:
    """Restore the newest session when --resume is present."""
    session_id = get_latest_session_id()
    if not session_id:
        print_info("No previous sessions found.")
        return

    session = load_session(session_id)
    if session is None:
        print_info(f"Could not load session {session_id}.")
        return

    agent.restore_session(session)


def main() -> None:
    """CLI entrypoint: one-shot prompt or interactive REPL."""
    os.chdir(SCRIPT_DIR)
    try:
        agent = create_agent()
    except RuntimeError as exc:
        print_error(exc)
        raise SystemExit(1) from exc

    if "--resume" in sys.argv:
        restore_latest_session(agent)

    args = [arg for arg in sys.argv[1:] if arg != "--resume"]
    prompt = " ".join(args).strip()
    if prompt:
        asyncio.run(agent.chat(prompt))
    else:
        asyncio.run(run_repl(agent))


if __name__ == "__main__":
    main()
