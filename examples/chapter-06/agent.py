"""Chapter 6 reference: streaming agent with a permission gate."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
import os
from pathlib import Path
import random
import signal
import sys
import time
from typing import Any, TypeVar
import uuid

import anthropic

from prompt import build_system_prompt
from session import get_latest_session_id, load_session, save_session
from tools import check_permission, execute_tool, tool_definitions
from ui import (
    print_assistant_text,
    print_error,
    print_info,
    print_tool_call,
    print_tool_result,
    print_user_prompt,
    print_welcome,
    print_confirmation,
    start_spinner,
    stop_spinner,
)


DEFAULT_BASE_URL = "https://api.deepseek.com/anthropic"
DEFAULT_MODEL = "claude-sonnet-4.6"
SCRIPT_DIR = Path(__file__).resolve().parent
T = TypeVar("T")


def response_usage(response: Any) -> tuple[int, int]:
    """Read usage safely from SDK response-like objects."""
    usage = getattr(response, "usage", None)
    return (
        int(getattr(usage, "input_tokens", 0) or 0),
        int(getattr(usage, "output_tokens", 0) or 0),
    )


def is_retryable(error: Exception) -> bool:
    """Return True for transient model/API failures worth retrying."""
    status = getattr(error, "status_code", None)
    if status in {429, 500, 502, 503, 529}:
        return True

    text = str(error).lower()
    return "overloaded" in text or "rate limit" in text or "timeout" in text


async def with_retry(
    operation: Callable[[], Awaitable[T]],
    *,
    max_retries: int = 3,
) -> T:
    """Run an async operation with exponential backoff for transient failures."""
    for attempt in range(max_retries + 1):
        try:
            return await operation()
        except Exception as exc:
            if attempt >= max_retries or not is_retryable(exc):
                raise

            delay = min(2**attempt, 30) + random.random()
            print_info(f"Retry {attempt + 1}/{max_retries} in {delay:.1f}s...")
            await asyncio.sleep(delay)

    raise RuntimeError("unreachable retry state")


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
        yolo: bool = False,
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
        self.yolo = yolo
        self._confirmed_commands: set[str] = set()
        self._read_file_state: dict[str, float] = {}
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
            start_spinner()
            try:
                response = await self._call_stream()
            finally:
                stop_spinner()

            input_tokens, output_tokens = response_usage(response)
            self.total_input_tokens += input_tokens
            self.total_output_tokens += output_tokens

            content = assistant_content(response.content)
            self.messages.append({"role": "assistant", "content": content})
            tool_results = self._execute_tool_calls(response.content)

            if not tool_results:
                break

            self.messages.append({"role": "user", "content": tool_results})

        self._auto_save()

    def _execute_tool_calls(self, blocks: Iterable[Any]) -> list[dict[str, Any]]:
        """Check permissions, execute allowed tools, and return tool_result blocks."""
        results: list[dict[str, Any]] = []

        for block in blocks:
            if block.type != "tool_use":
                continue

            print_tool_call(block.name, block.input)
            permission = check_permission(block.name, block.input)
            action = permission.get("action")

            if action == "deny":
                message = permission.get("message", "Permission denied.")
                print_info(f"Denied: {message}")
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": f"Action denied: {message}",
                    }
                )
                continue

            if action == "confirm" and not self.yolo:
                message = permission.get("message", "")
                if message not in self._confirmed_commands:
                    print_confirmation(message)
                    try:
                        answer = input("Allow? (y/n): ")
                    except EOFError:
                        answer = "n"

                    if not answer.lower().startswith("y"):
                        results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": "User denied this action.",
                            }
                        )
                        continue

                    self._confirmed_commands.add(message)

            result = execute_tool(block.name, block.input, self._read_file_state)
            print_tool_result(block.name, result)
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                }
            )

        return results

    async def _call_stream(self) -> Any:
        """Call the model with streaming and print text deltas immediately."""

        async def operation() -> Any:
            async with self.client.messages.stream(
                model=self.model,
                max_tokens=8192,
                system=self.system_prompt,
                messages=self.messages,
                tools=tool_definitions,
            ) as stream:
                first_text = True
                async for event in stream:
                    if getattr(event, "type", None) != "content_block_delta":
                        continue

                    delta = getattr(event, "delta", None)
                    text = getattr(delta, "text", None)
                    if text is None:
                        continue

                    if first_text:
                        stop_spinner()
                        print_assistant_text("\n")
                        first_text = False
                    print_assistant_text(text)

                if not first_text:
                    print_assistant_text("\n")

                return await stream.get_final_message()

        return await with_retry(operation)

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
        yolo="--yolo" in sys.argv,
    )


def main() -> None:
    """CLI entry point."""
    agent = create_agent()

    if "--resume" in sys.argv:
        session_id = get_latest_session_id()
        if session_id:
            data = load_session(session_id)
            if data:
                agent.restore_session(data)

    asyncio.run(run_repl(agent))


if __name__ == "__main__":
    main()
