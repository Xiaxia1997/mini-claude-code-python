"""Chapter 4 tool definitions.

This chapter keeps the tool surface intentionally small. The new idea is the
system prompt, not a larger permission-less toolbox.
"""

from pathlib import Path
from typing import Any


MAX_TOOL_RESULT_CHARS = 50_000


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

tool_definitions = [read_file_tool]


def read_file(file_path: str) -> str:
    """Read a UTF-8 text file and turn failures into tool results."""
    try:
        return Path(file_path).read_text(encoding="utf-8")
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


def execute_tool(name: str, args: dict[str, Any]) -> str:
    """Dispatch a model tool request to the matching Python function."""
    if name != "read_file":
        return f"Unknown tool: {name}"

    file_path = args.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        return "Error: read_file requires a non-empty file_path"

    return truncate_result(read_file(file_path))
