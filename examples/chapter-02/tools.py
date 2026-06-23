"""Chapter 2 tool definitions and execution functions."""

from pathlib import Path
from typing import Any


read_file_tool = {
    "name": "read_file",
    "description": "读取文件内容，返回文本",
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


def execute_tool(name: str, args: dict[str, Any]) -> str:
    """Dispatch a model tool request to the matching Python function."""
    if name == "read_file":
        file_path = args.get("file_path")
        if not isinstance(file_path, str) or not file_path:
            return "Error: read_file requires a non-empty file_path"
        return read_file(file_path)

    return f"Unknown tool: {name}"
