from importlib.util import module_from_spec, spec_from_file_location
import asyncio
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


def load_chapter_4_agent():
    chapter_dir = ROOT / "examples/chapter-04"
    for module_name in ("tools", "prompt", "session", "ui"):
        sys.modules.pop(module_name, None)
    sys.path.insert(0, str(chapter_dir))
    try:
        return load_module("chapter_04_agent", chapter_dir / "agent.py")
    finally:
        sys.path.remove(str(chapter_dir))


def load_chapter_4_session():
    return load_module(
        "chapter_04_session",
        ROOT / "examples/chapter-04/session.py",
    )


def load_chapter_agent(chapter: str):
    chapter_dir = ROOT / f"examples/chapter-{chapter}"
    for module_name in ("tools", "prompt", "session", "ui"):
        sys.modules.pop(module_name, None)
    sys.path.insert(0, str(chapter_dir))
    try:
        return load_module(f"chapter_{chapter}_agent", chapter_dir / "agent.py")
    finally:
        sys.path.remove(str(chapter_dir))


def load_chapter_tools(chapter: str):
    return load_module(
        f"chapter_{chapter}_tools",
        ROOT / f"examples/chapter-{chapter}/tools.py",
    )


class FakeStream:
    def __init__(self, events, final_message):
        self.events = events
        self.final_message = final_message

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def __aiter__(self):
        self._iter = iter(self.events)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration

    async def get_final_message(self):
        return self.final_message


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


def test_chapter_4_session_save_load_list_and_latest(
    tmp_path: Path,
) -> None:
    session = load_chapter_4_session()
    session.SESSION_DIR = tmp_path / "sessions"

    session.save_session(
        "old",
        {
            "metadata": {
                "id": "old",
                "startTime": "2026-06-23T09:00:00Z",
                "messageCount": 1,
            },
            "messages": [{"role": "user", "content": "old"}],
        },
    )
    session.save_session(
        "new",
        {
            "metadata": {
                "id": "new",
                "startTime": "2026-06-24T09:00:00Z",
                "messageCount": 2,
            },
            "messages": [{"role": "user", "content": "new"}],
        },
    )
    (session.SESSION_DIR / "broken.json").write_text("{", encoding="utf-8")

    assert session.load_session("old")["messages"][0]["content"] == "old"
    assert session.load_session("missing") is None
    assert {item["id"] for item in session.list_sessions()} == {"old", "new"}
    assert session.get_latest_session_id() == "new"


def test_chapter_4_agent_uses_async_client_and_auto_saves(monkeypatch) -> None:
    agent_module = load_chapter_4_agent()
    calls = []
    saved = {}

    class FakeMessages:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                usage=SimpleNamespace(input_tokens=7, output_tokens=11),
                content=[SimpleNamespace(type="text", text="收到")],
            )

    fake_client = SimpleNamespace(messages=FakeMessages())
    monkeypatch.setattr(
        agent_module.anthropic,
        "AsyncAnthropic",
        lambda **kwargs: fake_client,
    )
    monkeypatch.setattr(agent_module, "build_system_prompt", lambda: "SYSTEM")
    monkeypatch.setattr(
        agent_module,
        "save_session",
        lambda session_id, data: saved.update(
            {"session_id": session_id, "data": data}
        ),
    )
    monkeypatch.setattr(agent_module, "print_assistant_text", lambda text: None)

    agent = agent_module.Agent(
        api_key="test-key",
        base_url="https://example.test/anthropic",
        model="test-model",
    )
    asyncio.run(agent.chat("你好"))

    assert calls[0]["model"] == "test-model"
    assert calls[0]["system"] == "SYSTEM"
    assert calls[0]["messages"][0]["content"][0]["text"] == "你好"
    assert agent.total_input_tokens == 7
    assert agent.total_output_tokens == 11
    assert saved["session_id"] == agent.session_id
    assert saved["data"]["metadata"]["messageCount"] == 2
    assert saved["data"]["metadata"]["totalInputTokens"] == 7
    assert saved["data"]["metadata"]["totalOutputTokens"] == 11


def test_chapter_4_agent_continues_after_tool_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    agent_module = load_chapter_4_agent()
    sample = tmp_path / "sample.txt"
    sample.write_text("hello from chapter 4", encoding="utf-8")
    calls = []
    responses = [
        SimpleNamespace(
            usage=SimpleNamespace(input_tokens=5, output_tokens=3),
            content=[
                SimpleNamespace(
                    type="tool_use",
                    id="toolu_789",
                    name="read_file",
                    input={"file_path": str(sample)},
                )
            ],
        ),
        SimpleNamespace(
            usage=SimpleNamespace(input_tokens=6, output_tokens=4),
            content=[SimpleNamespace(type="text", text="读到了 chapter 4")],
        ),
    ]

    class FakeMessages:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return responses.pop(0)

    fake_client = SimpleNamespace(messages=FakeMessages())
    monkeypatch.setattr(
        agent_module.anthropic,
        "AsyncAnthropic",
        lambda **kwargs: fake_client,
    )
    monkeypatch.setattr(agent_module, "build_system_prompt", lambda: "SYSTEM")
    monkeypatch.setattr(agent_module, "save_session", lambda session_id, data: None)
    monkeypatch.setattr(agent_module, "print_tool_call", lambda name, inp: None)
    monkeypatch.setattr(agent_module, "print_tool_result", lambda name, result: None)
    monkeypatch.setattr(agent_module, "print_assistant_text", lambda text: None)

    agent = agent_module.Agent(
        api_key="test-key",
        base_url="https://example.test/anthropic",
        model="test-model",
    )
    asyncio.run(agent.chat("读取 sample.txt"))

    assert len(calls) == 2
    assert agent.messages[2] == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_789",
                "content": "hello from chapter 4",
            }
        ],
    }
    assert agent.messages[-1]["content"][0]["text"] == "读到了 chapter 4"


def test_chapter_4_restore_session_recovers_messages_and_token_counts(
    monkeypatch,
) -> None:
    agent_module = load_chapter_4_agent()
    monkeypatch.setattr(
        agent_module.anthropic,
        "AsyncAnthropic",
        lambda **kwargs: SimpleNamespace(messages=SimpleNamespace()),
    )
    monkeypatch.setattr(agent_module, "build_system_prompt", lambda: "SYSTEM")
    monkeypatch.setattr(agent_module, "print_info", lambda message: None)
    agent = agent_module.Agent(api_key="test-key", base_url=None)

    agent.restore_session(
        {
            "metadata": {
                "id": "restored",
                "startTime": "2026-06-24T10:00:00Z",
                "totalInputTokens": 12,
                "totalOutputTokens": 34,
            },
            "messages": [{"role": "user", "content": "之前的问题"}],
        }
    )

    assert agent.session_id == "restored"
    assert agent.session_start_time == "2026-06-24T10:00:00Z"
    assert agent.total_input_tokens == 12
    assert agent.total_output_tokens == 34
    assert agent.messages == [{"role": "user", "content": "之前的问题"}]


def test_chapter_5_streams_text_before_final_message(monkeypatch) -> None:
    agent_module = load_chapter_agent("05")
    printed = []
    calls = []

    final_message = SimpleNamespace(
        usage=SimpleNamespace(input_tokens=9, output_tokens=4),
        content=[SimpleNamespace(type="text", text="hello")],
    )
    events = [
        SimpleNamespace(
            type="content_block_delta",
            delta=SimpleNamespace(text="he"),
        ),
        SimpleNamespace(
            type="content_block_delta",
            delta=SimpleNamespace(text="llo"),
        ),
    ]

    class FakeMessages:
        def stream(self, **kwargs):
            calls.append(kwargs)
            return FakeStream(events, final_message)

    fake_client = SimpleNamespace(messages=FakeMessages())
    monkeypatch.setattr(
        agent_module.anthropic,
        "AsyncAnthropic",
        lambda **kwargs: fake_client,
    )
    monkeypatch.setattr(agent_module, "build_system_prompt", lambda: "SYSTEM")
    monkeypatch.setattr(agent_module, "save_session", lambda session_id, data: None)
    monkeypatch.setattr(agent_module, "start_spinner", lambda label="Thinking": None)
    monkeypatch.setattr(agent_module, "stop_spinner", lambda: None)
    monkeypatch.setattr(agent_module, "print_assistant_text", printed.append)

    agent = agent_module.Agent(api_key="test-key", base_url=None, model="test-model")
    asyncio.run(agent.chat("hi"))

    assert calls[0]["model"] == "test-model"
    assert calls[0]["system"] == "SYSTEM"
    assert "".join(printed).strip() == "hello"
    assert agent.total_input_tokens == 9
    assert agent.total_output_tokens == 4


def test_chapter_6_detects_dangerous_commands() -> None:
    tools = load_chapter_tools("06")

    assert tools.is_dangerous("rm -rf /")
    assert tools.is_dangerous("git push --force")
    assert tools.is_dangerous("sudo apt install nginx")
    assert not tools.is_dangerous("ls -la")
    assert tools.check_permission("read_file", {"file_path": "x.py"}) == {
        "action": "allow"
    }
    assert tools.check_permission("run_shell", {"command": "rm -rf /"}) == {
        "action": "confirm",
        "message": "rm -rf /",
    }


def test_chapter_7_budgets_old_tool_results(monkeypatch) -> None:
    agent_module = load_chapter_agent("07")
    monkeypatch.setattr(
        agent_module.anthropic,
        "AsyncAnthropic",
        lambda **kwargs: SimpleNamespace(messages=SimpleNamespace()),
    )
    monkeypatch.setattr(agent_module, "build_system_prompt", lambda: "SYSTEM")

    agent = agent_module.Agent(api_key="test-key", base_url=None)
    agent.effective_window = 100
    agent.last_input_token_count = 75
    agent.messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_1",
                    "content": "a" * 40_000,
                }
            ],
        }
    ]

    agent._budget_tool_results()

    content = agent.messages[0]["content"][0]["content"]
    assert len(content) < 20_000
    assert "budgeted" in content


def test_chapter_7_snips_stale_duplicate_tool_results(monkeypatch) -> None:
    agent_module = load_chapter_agent("07")
    monkeypatch.setattr(
        agent_module.anthropic,
        "AsyncAnthropic",
        lambda **kwargs: SimpleNamespace(messages=SimpleNamespace()),
    )
    monkeypatch.setattr(agent_module, "build_system_prompt", lambda: "SYSTEM")

    agent = agent_module.Agent(api_key="test-key", base_url=None)
    agent.effective_window = 100
    agent.last_input_token_count = 70
    agent.messages = [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_old",
                    "name": "read_file",
                    "input": {"file_path": "agent.py"},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_old",
                    "content": "old content",
                }
            ],
        },
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_new",
                    "name": "read_file",
                    "input": {"file_path": "agent.py"},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_new",
                    "content": "new content",
                }
            ],
        },
    ]

    agent._snip_stale_results()

    assert "snipped" in agent.messages[1]["content"][0]["content"]
    assert agent.messages[3]["content"][0]["content"] == "new content"


def test_chapter_7_compact_reuses_agent_system_prompt(monkeypatch) -> None:
    agent_module = load_chapter_agent("07")
    calls = []

    class FakeMessages:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                usage=SimpleNamespace(input_tokens=30, output_tokens=8),
                content=[SimpleNamespace(type="text", text="Summary text.")],
            )

    fake_client = SimpleNamespace(messages=FakeMessages())
    monkeypatch.setattr(
        agent_module.anthropic,
        "AsyncAnthropic",
        lambda **kwargs: fake_client,
    )
    monkeypatch.setattr(agent_module, "build_system_prompt", lambda: "SYSTEM")

    agent = agent_module.Agent(api_key="test-key", base_url=None)
    agent.messages = [
        {"role": "user", "content": [{"type": "text", "text": "旧问题"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "旧回答"}]},
    ]

    asyncio.run(agent._compact_conversation())

    assert calls[0]["system"] == "SYSTEM"
    assert calls[0]["messages"][0]["content"][0]["text"] == "旧问题"
    assert "Summarize the conversation" in calls[0]["messages"][-1]["content"][0]["text"]
    assert len(agent.messages) == 3
    assert "Summary text." in agent.messages[1]["content"][0]["text"]
