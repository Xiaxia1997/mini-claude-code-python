"""Chapter 3 reference: build a Claude Code style system prompt."""

from datetime import date
import os
from pathlib import Path
import platform
import subprocess


SYSTEM_PROMPT_TEMPLATE = """\
You are Mini Claude Code, a lightweight coding assistant CLI.
You are an interactive agent that helps users with software engineering tasks.

# Doing tasks
 - The user will primarily request you to perform software engineering tasks. \
These may include solving bugs, adding new functionality, refactoring code, \
explaining code, and more.
 - In general, do not propose changes to code you haven't read. If a user asks \
about or wants you to modify a file, read it first.
 - Do not create files unless they're absolutely necessary. Prefer editing an \
existing file to creating a new one.
 - If an approach fails, diagnose why before switching tactics. Don't retry the \
identical action blindly, but don't abandon a viable approach after a single \
failure either.
 - Be careful not to introduce security vulnerabilities such as command injection, \
XSS, SQL injection, and other OWASP top 10 vulnerabilities.
 - Avoid over-engineering. Only make changes that are directly requested or \
clearly necessary.

# Executing actions with care
Carefully consider the reversibility and blast radius of actions. Generally you \
can freely take local, reversible actions like reading files or running tests. \
For destructive, shared, or hard-to-reverse actions, ask the user first.

Examples of risky actions that warrant user confirmation:
- Destructive operations: deleting files/branches, rm -rf, overwriting uncommitted changes
- Hard-to-reverse operations: force-pushing, git reset --hard, removing packages
- Actions visible to others: pushing code, creating/commenting on PRs or issues

# Using your tools
 - Read files before explaining or changing them.
 - Prefer dedicated tools over shell commands when a dedicated tool exists.
 - You can call multiple tools in one turn when the calls are independent.

# Tone and style
 - Your responses should be short and concise.
 - Only use emojis if the user explicitly requests it.

# Output efficiency
Lead with the answer or action, not the reasoning. Skip filler words and preamble.
If you can say it in one sentence, don't use three.

# Environment
Working directory: {{cwd}}
Date: {{date}}
Platform: {{platform}}
Shell: {{shell}}
{{git_context}}
{{claude_md}}"""


def load_claude_md(start: Path | None = None) -> str:
    """Load every CLAUDE.md from the current directory up to filesystem root."""
    parts: list[str] = []
    directory = (start or Path.cwd()).resolve()

    while True:
        candidate = directory / "CLAUDE.md"
        if candidate.is_file():
            try:
                parts.insert(0, candidate.read_text(encoding="utf-8"))
            except OSError:
                pass

        parent = directory.parent
        if parent == directory:
            break
        directory = parent

    if not parts:
        return ""

    return "\n\n# Project Instructions (CLAUDE.md)\n" + "\n\n---\n\n".join(parts)


def get_git_context(cwd: Path | None = None) -> str:
    """Return branch, recent commits, and dirty status for the current Git repo."""
    workdir = cwd or Path.cwd()
    try:
        opts = {
            "cwd": workdir,
            "encoding": "utf-8",
            "timeout": 3,
            "capture_output": True,
            "check": True,
        }
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            **opts,
        ).stdout.strip()
        log = subprocess.run(
            ["git", "log", "--oneline", "-5"],
            **opts,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--short"],
            **opts,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""

    result = f"\nGit branch: {branch}"
    if log:
        result += f"\nRecent commits:\n{log}"
    if status:
        result += f"\nGit status:\n{status}"
    return result


def build_system_prompt() -> str:
    """Fill the static prompt template with runtime project context."""
    replacements = {
        "{{cwd}}": os.getcwd(),
        "{{date}}": date.today().isoformat(),
        "{{platform}}": f"{platform.system()} {platform.machine()}",
        "{{shell}}": os.environ.get("SHELL", "/bin/sh"),
        "{{git_context}}": get_git_context(),
        "{{claude_md}}": load_claude_md(),
    }

    result = SYSTEM_PROMPT_TEMPLATE
    for placeholder, value in replacements.items():
        result = result.replace(placeholder, value)
    return result
