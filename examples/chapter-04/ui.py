"""Chapter 4 reference: small terminal UI helpers."""

from __future__ import annotations

import sys
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.text import Text


console = Console(highlight=False, soft_wrap=True)

ACCENT = "bright_cyan"
MUTED = "dim"
WARNING = "yellow"
ERROR = "red"

TOOL_ICONS = {
    "read_file": "📖",
    "write_file": "✏️",
    "edit_file": "🔧",
    "list_files": "📁",
    "grep_search": "🔍",
    "run_shell": "💻",
}


def print_welcome() -> None:
    """Print the REPL welcome panel."""
    title = Text()
    title.append("Mini Claude Code", style=f"bold {ACCENT}")
    title.append("\nChapter 4 · CLI & Session", style=MUTED)

    body = Text()
    body.append("Type a request, or ", style=MUTED)
    body.append("exit", style="bold")
    body.append(" to quit.\n", style=MUTED)
    body.append("Commands  ", style=MUTED)
    body.append("/clear", style="bold")
    body.append("  ")
    body.append("/cost", style="bold")

    console.print()
    console.print(
        Panel(
            body,
            title=title,
            title_align="left",
            border_style=ACCENT,
            padding=(1, 2),
        )
    )


def print_user_prompt() -> None:
    """Print a prompt marker without consuming input."""
    console.print(f"\n[bold {ACCENT}]╭─[/bold {ACCENT}] [dim]you[/dim]")
    console.print(f"[bold {ACCENT}]╰─>[/bold {ACCENT}] ", end="")


def print_assistant_text(text: str) -> None:
    """Stream assistant text to stdout without extra formatting."""
    sys.stdout.write(text)
    sys.stdout.flush()


def print_tool_call(name: str, tool_input: dict[str, Any]) -> None:
    """Show which tool the model requested before executing it."""
    icon = TOOL_ICONS.get(name, "◆")
    summary = _get_tool_summary(name, tool_input)

    header = Text()
    header.append(f"{icon} ", style=WARNING)
    header.append(name, style=f"bold {WARNING}")
    if summary:
        header.append(f"  {summary}", style=MUTED)

    console.print()
    console.print(
        Panel(
            header,
            title="[dim]tool call[/dim]",
            title_align="left",
            border_style=WARNING,
            padding=(0, 1),
        )
    )


def print_tool_result(name: str, result: str) -> None:
    """Show a bounded preview of a tool result."""
    preview = str(result)
    max_len = 500
    if len(preview) > max_len:
        preview = preview[:max_len] + f"\n... ({len(preview)} chars total)"

    console.print(
        Panel(
            preview,
            title=f"[dim]{name} result[/dim]",
            title_align="left",
            border_style="dim",
            padding=(0, 1),
        )
    )


def print_error(message: object) -> None:
    """Print an error panel."""
    console.print()
    console.print(
        Panel(
            str(message),
            title=f"[bold {ERROR}]Error[/bold {ERROR}]",
            title_align="left",
            border_style=ERROR,
            padding=(0, 1),
        )
    )


def print_info(message: object) -> None:
    """Print a compact informational line."""
    console.print(f"\n[bold {ACCENT}]•[/bold {ACCENT}] [dim]{message}[/dim]")


def _get_tool_summary(name: str, tool_input: dict[str, Any]) -> str:
    """Extract a short, human-readable summary from tool arguments."""
    if name in {"read_file", "write_file", "edit_file"}:
        return str(tool_input.get("file_path", ""))
    if name == "list_files":
        return str(tool_input.get("path", "."))
    if name == "grep_search":
        return f'"{tool_input.get("pattern", "")}" in {tool_input.get("path", ".")}'
    if name == "run_shell":
        command = str(tool_input.get("command", ""))
        return command[:60] + "..." if len(command) > 60 else command
    return ""
