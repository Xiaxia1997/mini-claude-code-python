from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import subprocess
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


def load_chapter_3_agent():
    chapter_dir = ROOT / "examples/chapter-03"
    sys.modules.pop("tools", None)
    sys.modules.pop("prompt", None)
    sys.path.insert(0, str(chapter_dir))
    try:
        return load_module("chapter_03_agent", chapter_dir / "agent.py")
    finally:
        sys.path.remove(str(chapter_dir))


def load_chapter_3_prompt():
    return load_module(
        "chapter_03_prompt",
        ROOT / "examples/chapter-03/prompt.py",
    )


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


def test_chapter_3_build_system_prompt_replaces_runtime_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    prompt = load_chapter_3_prompt()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SHELL", "/bin/zsh")
    prompt.get_git_context = lambda: "\nGit branch: test-branch"
    prompt.load_claude_md = lambda: "\n\n# Project Instructions (CLAUDE.md)\n默认中文"

    system_prompt = prompt.build_system_prompt()

    assert "{{" not in system_prompt
    assert f"Working directory: {tmp_path}" in system_prompt
    assert "Shell: /bin/zsh" in system_prompt
    assert "Git branch: test-branch" in system_prompt
    assert "默认中文" in system_prompt


def test_chapter_3_load_claude_md_walks_up_directory_tree(
    tmp_path: Path,
    monkeypatch,
) -> None:
    prompt = load_chapter_3_prompt()
    project = tmp_path / "project"
    child = project / "src" / "api"
    child.mkdir(parents=True)
    (project / "CLAUDE.md").write_text("项目根规则", encoding="utf-8")
    (child / "CLAUDE.md").write_text("子目录规则", encoding="utf-8")
    monkeypatch.chdir(child)

    claude_md = prompt.load_claude_md()

    assert "# Project Instructions (CLAUDE.md)" in claude_md
    assert claude_md.index("项目根规则") < claude_md.index("子目录规则")


def test_chapter_3_get_git_context_returns_empty_outside_git(
    tmp_path: Path,
    monkeypatch,
) -> None:
    prompt = load_chapter_3_prompt()
    monkeypatch.chdir(tmp_path)

    assert prompt.get_git_context() == ""


def test_chapter_3_get_git_context_includes_branch_and_recent_commit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    prompt = load_chapter_3_prompt()
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True)
    (tmp_path / "tracked.txt").write_text("seed", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test User",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "seed commit",
        ],
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / "dirty.txt").write_text("dirty", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    git_context = prompt.get_git_context()

    assert "Git branch: main" in git_context
    assert "Recent commits:" in git_context
    assert "seed commit" in git_context
    assert "Git status:" in git_context
    assert "dirty.txt" in git_context


def test_chapter_3_agent_passes_system_prompt_to_model() -> None:
    agent = load_chapter_3_agent()

    class FakeMessages:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="收到")]
            )

    fake_messages = FakeMessages()
    client = SimpleNamespace(messages=fake_messages)
    messages = [{"role": "user", "content": "你好"}]
    output = []

    agent.run_agent_turn(
        client,
        messages,
        system_prompt="SYSTEM FROM PROMPT",
        output_fn=output.append,
    )

    assert fake_messages.calls[0]["system"] == "SYSTEM FROM PROMPT"
    assert output == ["收到"]


def test_chapter_3_tool_loop_still_returns_tool_result(tmp_path: Path) -> None:
    agent = load_chapter_3_agent()
    sample = tmp_path / "sample.txt"
    sample.write_text("hello from chapter 3", encoding="utf-8")

    responses = [
        SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="tool_use",
                    id="toolu_456",
                    name="read_file",
                    input={"file_path": str(sample)},
                )
            ]
        ),
        SimpleNamespace(
            content=[SimpleNamespace(type="text", text="读到了 chapter 3")]
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

    agent.run_agent_turn(
        client,
        messages,
        system_prompt="SYSTEM",
        output_fn=output.append,
    )

    assert len(fake_messages.calls) == 2
    assert messages[2] == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_456",
                "content": "hello from chapter 3",
            }
        ],
    }
    assert output == ["读到了 chapter 3"]
