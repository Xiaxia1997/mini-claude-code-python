"""Chapter 6 tool definitions with a small permission layer.

The regex checks are deliberately simple: they teach where permission gates
sit in the agent loop without pulling in shell AST parsing.
"""

import os
from pathlib import Path
import re
import subprocess
from typing import Any


MAX_TOOL_RESULT_CHARS = 50_000

DANGEROUS_PATTERNS = [
    re.compile(r"\brm\s"),
    re.compile(r"\bgit\s+(push|reset|clean|checkout\s+\.)"),
    re.compile(r"\bsudo\b"),
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\s"),
    re.compile(r">\s*/dev/"),
    re.compile(r"\bkill\b"),
    re.compile(r"\bpkill\b"),
    re.compile(r"\breboot\b"),
    re.compile(r"\bshutdown\b"),
    re.compile(r"\bdel\s", re.IGNORECASE),
    re.compile(r"\brmdir\s", re.IGNORECASE),
    re.compile(r"\bformat\s", re.IGNORECASE),
    re.compile(r"\btaskkill\s", re.IGNORECASE),
    re.compile(r"\bRemove-Item\s", re.IGNORECASE),
    re.compile(r"\bStop-Process\s", re.IGNORECASE),
]

READ_TOOLS = {"read_file", "list_files", "grep_search"}


def is_dangerous(command: str) -> bool:
    """Return True when a shell command matches a dangerous pattern."""
    return any(pattern.search(command) for pattern in DANGEROUS_PATTERNS)


def check_permission(tool_name: str, args: dict[str, Any]) -> dict[str, str]:
    """Return allow/confirm/deny for one tool call."""
    if tool_name in READ_TOOLS:
        return {"action": "allow"}

    if tool_name == "run_shell":
        command = str(args.get("command", ""))
        if is_dangerous(command):
            return {"action": "confirm", "message": command}

    return {"action": "allow"}


read_file_tool = {
    "name": "read_file",
    "description": "读取 UTF-8 文本文件内容",
    "input_schema": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "要读取的文件路径",
            }
        },
        "required": ["file_path"],
    },
}

list_files_tool = {
    "name": "list_files",
    "description": "列出目录中的文件和子目录",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "要列出的目录路径，默认当前目录",
            }
        },
        "required": [],
    },
}

write_file_tool = {
    "name": "write_file",
    "description": "写入 UTF-8 文本文件，文件不存在会创建，已存在会覆盖",
    "input_schema": {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "目标文件路径"},
            "content": {"type": "string", "description": "完整文件内容"},
        },
        "required": ["file_path", "content"],
    },
}

edit_file_tool = {
    "name": "edit_file",
    "description": "精确替换文件中的一段字符串，old_string 必须唯一",
    "input_schema": {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "目标文件路径"},
            "old_string": {"type": "string", "description": "要替换的旧内容"},
            "new_string": {"type": "string", "description": "替换后的新内容"},
        },
        "required": ["file_path", "old_string", "new_string"],
    },
}

grep_search_tool = {
    "name": "grep_search",
    "description": "在文件中搜索匹配文本，返回匹配行及行号",
    "input_schema": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "要搜索的文本或正则"},
            "path": {"type": "string", "description": "搜索目录，默认当前目录"},
        },
        "required": ["pattern"],
    },
}

run_shell_tool = {
    "name": "run_shell",
    "description": "执行 shell 命令，返回 stdout/stderr",
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的 shell 命令"}
        },
        "required": ["command"],
    },
}

tool_definitions = [
    read_file_tool,
    list_files_tool,
    write_file_tool,
    edit_file_tool,
    grep_search_tool,
    run_shell_tool,
]


def read_file(file_path: str) -> str:
    """Read a UTF-8 text file and turn failures into tool results."""
    try:
        return Path(file_path).read_text(encoding="utf-8")
    except Exception as exc:
        return f"Error: {exc}"


def list_files(path: str = ".") -> str:
    """List one directory in a deterministic order."""
    try:
        return "\n".join(sorted(item.name for item in Path(path).iterdir()))
    except Exception as exc:
        return f"Error: {exc}"


def write_file(file_path: str, content: str) -> str:
    """Write a UTF-8 file."""
    try:
        Path(file_path).write_text(content, encoding="utf-8")
        return f"Successfully wrote to {file_path}"
    except Exception as exc:
        return f"Error: {exc}"


def edit_file(file_path: str, old_string: str, new_string: str) -> str:
    """Replace one unique string in a UTF-8 text file."""
    try:
        path = Path(file_path)
        content = path.read_text(encoding="utf-8")
        count = content.count(old_string)
        if count == 0:
            return f"Error: old_string not found in {file_path}"
        if count > 1:
            return f"Error: old_string found {count} times; make it unique"
        path.write_text(content.replace(old_string, new_string, 1), encoding="utf-8")
        return f"Successfully edited {file_path}"
    except Exception as exc:
        return f"Error: {exc}"


def grep_search(pattern: str, path: str = ".") -> str:
    """Use grep for a tiny, dependency-free search tool."""
    try:
        result = subprocess.run(
            ["grep", "-rn", "--color=never", pattern, path],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode == 1:
            return "No matches found."
        if result.returncode != 0:
            return f"Exit code {result.returncode}\n{result.stderr}"
        return result.stdout or "No matches found."
    except Exception as exc:
        return f"Error: {exc}"


def run_shell(command: str) -> str:
    """Run a shell command after the agent permission gate has approved it."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            return f"Exit code {result.returncode}\n{result.stderr}"
        return result.stdout or "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: command timed out"
    except Exception as exc:
        return f"Error: {exc}"


def truncate_result(result: str) -> str:
    """Keep very large tool results from flooding the next model call."""
    if len(result) <= MAX_TOOL_RESULT_CHARS:
        return result

    half = MAX_TOOL_RESULT_CHARS // 2
    return (
        result[:half]
        + f"\n\n... ({len(result)} chars total, truncated) ...\n\n"
        + result[-half:]
    )


def execute_tool(
    name: str,
    args: dict[str, Any],
    read_file_state: dict[str, float] | None = None,
) -> str:
    """Dispatch a model tool request to the matching Python function."""
    handlers = {
        "read_file": read_file,
        "list_files": list_files,
        "write_file": write_file,
        "edit_file": edit_file,
        "grep_search": grep_search,
        "run_shell": run_shell,
    }
    handler = handlers.get(name)
    if handler is None:
        return f"Unknown tool: {name}"

    try:
        if name == "read_file":
            result = handler(**args)
            if read_file_state is not None and not result.startswith("Error"):
                abs_path = os.path.realpath(args["file_path"])
                read_file_state[abs_path] = os.path.getmtime(abs_path)
            return truncate_result(result)

        if name in {"write_file", "edit_file"} and read_file_state is not None:
            abs_path = os.path.realpath(args["file_path"])
            if os.path.exists(abs_path):
                if abs_path not in read_file_state:
                    verb = "writing" if name == "write_file" else "editing"
                    return (
                        "Error: You must read this file before "
                        f"{verb}. Use read_file first."
                    )
                if os.path.getmtime(abs_path) != read_file_state[abs_path]:
                    verb = "writing" if name == "write_file" else "editing"
                    return (
                        f"Warning: {args['file_path']} was modified externally "
                        f"since your last read. Please read_file again before {verb}."
                    )

        result = handler(**args)
        if (
            name in {"write_file", "edit_file"}
            and read_file_state is not None
            and not result.startswith("Error")
        ):
            abs_path = os.path.realpath(args["file_path"])
            read_file_state[abs_path] = os.path.getmtime(abs_path)
        return truncate_result(result)
    except TypeError as exc:
        return f"Error: invalid arguments for {name}: {exc}"
    except Exception as exc:
        return f"Error: {exc}"
