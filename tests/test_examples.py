from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = spec_from_file_location(name, path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_chapter_2_agent():
    chapter_dir = ROOT / "examples/chapter-02"
    sys.modules.pop("tools", None)
    sys.path.insert(0, str(chapter_dir))
    try:
        return load_module("chapter_02_agent", chapter_dir / "agent.py")
    finally:
        sys.path.remove(str(chapter_dir))


def test_chapter_1_keeps_only_text_blocks() -> None:
    agent = load_module(
        "chapter_01_agent",
        ROOT / "examples/chapter-01/agent.py",
    )
    blocks = [
        SimpleNamespace(type="thinking", thinking="hidden"),
        SimpleNamespace(type="text", text="你好"),
    ]

    assert agent.text_content(blocks) == [{"type": "text", "text": "你好"}]


def test_read_file_returns_file_content(tmp_path: Path) -> None:
    tools = load_module(
        "chapter_02_tools",
        ROOT / "examples/chapter-02/tools.py",
    )
    sample = tmp_path / "sample.txt"
    sample.write_text("hello agent", encoding="utf-8")

    assert tools.read_file(str(sample)) == "hello agent"


def test_read_file_returns_error_instead_of_raising(tmp_path: Path) -> None:
    tools = load_module(
        "chapter_02_tools_missing",
        ROOT / "examples/chapter-02/tools.py",
    )

    result = tools.read_file(str(tmp_path / "missing.txt"))

    assert result.startswith("Error:")


def test_execute_tool_dispatches_read_file(tmp_path: Path) -> None:
    tools = load_module(
        "chapter_02_tools_dispatch",
        ROOT / "examples/chapter-02/tools.py",
    )
    sample = tmp_path / "sample.txt"
    sample.write_text("tool result", encoding="utf-8")

    assert tools.execute_tool(
        "read_file",
        {"file_path": str(sample)},
    ) == "tool result"
    assert tools.execute_tool("unknown", {}) == "Unknown tool: unknown"


def test_chapter_2_serializes_text_and_tool_use_blocks() -> None:
    agent = load_chapter_2_agent()

    blocks = [
        SimpleNamespace(type="thinking", thinking="hidden"),
        SimpleNamespace(type="text", text="我来读取"),
        SimpleNamespace(
            type="tool_use",
            id="toolu_123",
            name="read_file",
            input={"file_path": "agent.py"},
        ),
    ]

    assert agent.assistant_content(blocks) == [
        {"type": "text", "text": "我来读取"},
        {
            "type": "tool_use",
            "id": "toolu_123",
            "name": "read_file",
            "input": {"file_path": "agent.py"},
        },
    ]


def test_chapter_2_runs_tool_loop_until_text_response(tmp_path: Path) -> None:
    agent = load_chapter_2_agent()
    sample = tmp_path / "sample.txt"
    sample.write_text("hello from tool", encoding="utf-8")

    responses = [
        SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="tool_use",
                    id="toolu_123",
                    name="read_file",
                    input={"file_path": str(sample)},
                )
            ]
        ),
        SimpleNamespace(
            content=[SimpleNamespace(type="text", text="文件内容是 hello from tool")]
        ),
    ]

    class FakeMessages:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return responses.pop(0)

    fake_messages = FakeMessages()
    client = SimpleNamespace(messages=fake_messages)
    messages = [{"role": "user", "content": "读取 sample.txt"}]
    output = []

    agent.run_agent_turn(client, messages, output_fn=output.append)

    assert len(fake_messages.calls) == 2
    assert messages[1]["role"] == "assistant"
    assert messages[2] == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_123",
                "content": "hello from tool",
            }
        ],
    }
    assert messages[3]["role"] == "assistant"
    assert output == ["文件内容是 hello from tool"]
