# 4. CLI 与会话 — 让 Agent 像个正式工具

> 本章参考代码：
> [`agent.py`](../examples/chapter-04/agent.py) ·
> [`prompt.py`](../examples/chapter-04/prompt.py) ·
> [`tools.py`](../examples/chapter-04/tools.py) ·
> [`session.py`](../examples/chapter-04/session.py) ·
> [`ui.py`](../examples/chapter-04/ui.py)

## 本章目标

到目前为止，你的 Agent 运行方式是：

```bash
python agent.py
```

一个裸的 `while True` + `input(">")`——没有命令行参数，没有快捷命令，退出就丢失所有对话，Ctrl+C 直接崩溃。

本章把它升级成一个像样的 CLI 工具：

| 能力 | 之前 | 之后 |
|---|---|---|
| 运行方式 | `python agent.py` | `python agent.py "fix bug"` |
| 退出方式 | 输入 `exit` | `exit` / Ctrl+C 两次 |
| 对话记录 | 退出就丢 | 自动保存到 JSON 文件 |
| 恢复对话 | 不支持 | `--resume` 继续上次 |
| REPL 命令 | 无 | `/clear` `/cost` 等 |
| 异步 | 同步阻塞 | `async/await` + `asyncio.run()` |

本章还会引入 **async/await**——这是后面流式输出（Ch5）的前置基础。

### 文件分工更新

Ch2 已经拆出了 `tools.py`，Ch3 拆出了 `prompt.py`。本章在此基础上再新增两个文件：

| 文件 | 状态 | 负责什么 |
|---|---|---|
| `tools.py` | Ch2 已有 | 定义工具 + 执行工具 |
| `prompt.py` | Ch3 已有 | 构造 system prompt |
| `ui.py` | **本章新增** | 所有终端输出格式化（颜色、图标、工具结果截断） |
| `session.py` | **本章新增** | 会话保存/加载/恢复 |
| `agent.py` | **本章重写** | Agent 类（async 版），对话循环 + REPL |

---

## Step 1：创建 ui.py — 统一输出格式

> 文件：`ui.py`（新文件）

之前工具调用的输出就是裸的 `print()`，看不出哪个是工具、哪个是回复。现在把所有终端输出集中到一个文件里，用颜色和图标让输出更清晰。

`rich` 库已经写在 `pyproject.toml` 的依赖里。按 README 用 `pip install -e .` 安装后，可以直接使用。

```python
import sys
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console(highlight=False, soft_wrap=True)

ACCENT = "bright_cyan"
MUTED = "dim"
WARNING = "yellow"
ERROR = "red"


def print_welcome():
    title = Text()
    title.append("Mini Claude Code", style=f"bold {ACCENT}")
    title.append("\nfrom scratch", style=MUTED)

    body = Text()
    body.append("Type your request, or ", style=MUTED)
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


def print_user_prompt():
    console.print(f"\n[bold {ACCENT}]╭─[/bold {ACCENT}] [dim]you[/dim]")
    console.print(f"[bold {ACCENT}]╰─>[/bold {ACCENT}] ", end="")


def print_assistant_text(text):
    sys.stdout.write(text)
    sys.stdout.flush()


def print_tool_call(name, inp):
    icon = TOOL_ICONS.get(name, "◆")
    summary = _get_tool_summary(name, inp)
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


def print_tool_result(name, result):
    result = str(result)
    max_len = 500
    if len(result) > max_len:
        result = result[:max_len] + f"\n  ... ({len(result)} chars total)"
    console.print(
        Panel(
            result,
            title=f"[dim]{name} result[/dim]",
            title_align="left",
            border_style="dim",
            padding=(0, 1),
        )
    )


def print_error(msg):
    console.print()
    console.print(
        Panel(
            str(msg),
            title=f"[bold {ERROR}]Error[/bold {ERROR}]",
            title_align="left",
            border_style=ERROR,
            padding=(0, 1),
        )
    )


def print_info(msg):
    console.print(f"\n[bold {ACCENT}]•[/bold {ACCENT}] [dim]{msg}[/dim]")


# ── 工具图标映射 ──

TOOL_ICONS = {
    "read_file": "📖",
    "write_file": "✏️",
    "edit_file": "🔧",
    "list_files": "📁",
    "grep_search": "🔍",
    "run_shell": "💻",
}


def _get_tool_summary(name, inp):
    """从工具输入参数中提取一句话摘要，显示在工具名旁边"""
    if name in ("read_file", "write_file", "edit_file"):
        return inp.get("file_path", "")  # 文件类工具显示路径
    if name == "list_files":
        return inp.get("path", ".")
    if name == "grep_search":
        return f'"{inp.get("pattern", "")}" in {inp.get("path", ".")}'
    if name == "run_shell":
        cmd = inp.get("command", "")
        return cmd[:60] + "..." if len(cmd) > 60 else cmd  # 命令太长截断
    return ""
```

### rich 的几个核心对象

`Console` 负责真正输出；`Text` 负责一段文字内部的不同样式；`Panel` 负责画边框，把欢迎信息、工具调用、错误信息分成清楚的区域。

`[bold cyan]文本[/bold cyan]` 这种是 rich 的标记语法。`[dim]` 是灰色，`[yellow]` 是黄色。`end=""` 表示输出后不换行，因为提示符后面要紧跟用户输入。

---

## Step 2：创建 session.py — 会话保存和恢复

> 文件：`session.py`（新文件）

现在每次退出程序，对话记录就没了。我们要实现**会话持久化**：把消息历史存到文件里，下次可以接着聊。

### 想一想：resume 需要哪些操作？

想象一个游戏的存档系统。你需要：

```text
┌─────────────────────────────────────────────┐
│  会话持久化 = 4 个基本操作                     │
│                                             │
│  1. 存档（save）    → 把对话写入文件           │
│  2. 读档（load）    → 按 ID 取回某次对话       │
│  3. 列出存档（list）→ 看看有哪些历史会话        │
│  4. 取最新（latest）→ --resume 时自动找到最近的 │
│                                             │
│  流程：                                      │
│  save ──写入──→ 磁盘上的 JSON 文件            │
│  list ──扫描──→ 所有 JSON 文件的元信息         │
│  latest ─调用 list─→ 按时间排序 → 取第一个 ID  │
│  load ──按 ID──→ 读回完整对话                 │
└─────────────────────────────────────────────┘
```

这四个函数各自独立，但组合起来就实现了 `--resume`：

### 先想清楚：要存什么才能恢复对话？

写代码之前，先从需求倒推数据结构。

**恢复对话**需要什么？——完整的消息历史，塞回 Agent 就能接着聊。（Ch1-3 里我们叫它 `context`，从本章开始改叫 `messages`——跟 Anthropic API 的参数名保持一致。）

**列出历史会话**需要什么？——每次对话的摘要信息：哪次对话（`id`）、什么时候开始的（`startTime`）、用的什么模型（`model`）、聊了多少轮（`messageCount`）。这些摘要不需要加载完整对话就能展示。

所以我们设计两层结构：

```json
{
  "metadata": {
    "id": "abc123",
    "model": "claude-sonnet-4.6",
    "startTime": "2025-06-24T10:30:00Z",
    "messageCount": 5,
    "totalInputTokens": 1234,
    "totalOutputTokens": 567
  },
  "messages": [
    {"role": "user", "content": [{"type": "text", "text": "你好"}]},
    {"role": "assistant", "content": [{"type": "text", "text": "你好！有什么可以帮你的？"}]},
    ...
  ]
}
```

- **metadata**：摘要，很小 → `list_sessions()` 只读这部分，快速展示列表
- **messages**：完整对话，可能很长 → 只有 `load_session()` 恢复时才需要

数据结构定好了，接下来写代码。（实际写入这个结构的代码在 Step 3 的 `_auto_save()` 里，Step 2 先实现读取的部分。）

### 2.1 存档：save_session()

最基础的操作——把数据写成 JSON 文件：

```python
import json
from pathlib import Path

SESSION_DIR = Path.home() / ".mini-claude" / "sessions"


def save_session(session_id, data):
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    path = SESSION_DIR / f"{session_id}.json"
    path.write_text(json.dumps(data, indent=2, default=str))
```

逐行看：

- `SESSION_DIR.mkdir(parents=True, exist_ok=True)`：确保目录存在。`parents=True` 递归创建父目录（如果 `.mini-claude` 也不存在就一起建），`exist_ok=True` 目录已存在不报错
- `f"{session_id}.json"`：f-string，如果 session_id 是 `"abc123"`，结果就是 `"abc123.json"`
- `json.dumps(data, indent=2, default=str)`：Python 对象 → JSON 字符串。`indent=2` 格式化输出方便人阅读，`default=str` 是兜底——遇到 `json.dumps` 不认识的类型（比如 `Path` 对象、`datetime`），一律转成字符串，而不是报错崩溃

### 2.2 读档：load_session()

按 ID 找到文件，读回来：

```python
def load_session(session_id):
    path = SESSION_DIR / f"{session_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None
```

两层防御：
1. `path.exists()` 检查文件是否存在——用户可能手动删了文件
2. `try/except` 兜底解析失败——文件可能损坏（比如上次写到一半程序崩了，JSON 不完整），`json.loads()` 会报错。返回 `None` 告诉调用方"加载失败了"，但不会让整个程序崩溃

### 2.3 列出存档：list_sessions()

扫描目录下所有 JSON 文件，提取每个会话的元信息：

```python
def list_sessions():
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for f in SESSION_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            if "metadata" in data:
                results.append(data["metadata"])
        except Exception:
            pass
    return results
    # 返回值示例：
    # [
    #     {"id": "abc123", "model": "claude-sonnet-4.6",
    #      "startTime": "2025-06-24T10:30:00Z", "messageCount": 5},
    #     {"id": "def456", "model": "claude-sonnet-4.6",
    #      "startTime": "2025-06-23T08:00:00Z", "messageCount": 12},
    # ]
    # → 每个元素是一个字典，只有摘要信息（id、模型、时间、消息数）
    #   不包含完整的对话内容，所以读起来很快
```

这里有个新东西——`glob("*.json")`：

```python
# glob 是"通配符匹配"，* 匹配任意字符
# *.json → 匹配所有以 .json 结尾的文件
for f in SESSION_DIR.glob("*.json"):
    print(f.name)
# 输出：abc123.json, def456.json, ...
```

函数只提取 `metadata`（ID、模型、时间、消息数），不返回完整消息历史——列表只需要摘要信息，不需要加载所有对话内容。

### 2.4 取最新：get_latest_session_id()

`--resume` 的核心——从所有存档中找到最近一次的 ID：

```python
def get_latest_session_id():
    sessions = list_sessions()
    if not sessions:
        return None
    sessions.sort(key=lambda s: s.get("startTime", ""), reverse=True)
    return sessions[0].get("id")
```

这里有两个新概念：

**`lambda` 匿名函数**：

```python
# 普通函数
def get_start_time(s):
    return s.get("startTime", "")

# lambda 等价写法——一行搞定的小函数
get_start_time = lambda s: s.get("startTime", "")

# 在 sort 里用 lambda 告诉 Python "按什么排序"
sessions.sort(key=lambda s: s.get("startTime", ""))
```

`lambda s: s.get("startTime", "")` 意思是：给我一个会话 `s`，我返回它的 `startTime` 字段。`sort` 就按这个值来排。

**`reverse=True`**：

```python
# 默认升序（旧 → 新）
[1, 3, 2].sort()           # → [1, 2, 3]

# reverse=True 降序（新 → 旧）
[1, 3, 2].sort(reverse=True)  # → [3, 2, 1]
```

降序排列后，`sessions[0]` 就是最新的那一个。

### 四个函数怎么串起来

回到开头的流程图，现在你理解每个函数了，再看一遍整个 resume 过程：

```text
python agent.py --resume
│
├─ get_latest_session_id()
│   ├─ list_sessions()
│   │   ├─ glob("*.json") 扫描所有存档文件
│   │   └─ 提取每个文件的 metadata → [{id, startTime, ...}, ...]
│   ├─ 按 startTime 降序排序
│   └─ 返回第一个的 id（最新的）
│
├─ load_session("abc123")
│   ├─ 读取 abc123.json 文件
│   └─ JSON 字符串 → Python 字典
│
└─ agent.restore_session(data)
    └─ 把 messages 塞回 self.messages → 对话恢复！
```

而每轮对话结束时，`_auto_save()` 自动调用 `save_session()` 把当前状态写入文件，形成闭环。

---

## Step 3：重写 agent.py — Agent 类 + async

> 文件：`agent.py`（重写）

这是本章最大的改动，同时做两件事：
1. 把裸的 `while True` 循环升级成一个 **Agent 类**
2. 从同步改为 **async/await**

### 为什么要用类

之前所有状态都是全局变量（`context`、`client`）。现在状态越来越多（消息历史、token 计数、session ID、输出缓冲），用类把它们归到一起。


### 为什么现在引入 async

Ch1-Ch3 用的都是同步代码——`client.messages.create()` 调一次，程序就卡在那等 API 返回，什么都干不了。Ch5 流式输出需要"一边收数据一边显示"，同步做不到，必须用 async。趁现在代码量小，改动成本低。

#### 先理解问题：同步为什么不行？

```python
# 同步版：程序执行到这里就"卡住"了，等 API 返回（可能 5-10 秒）
# 这期间整个程序冻结，连 Ctrl+C 都反应迟钝
response = client.messages.create(...)
```

#### async def：声明"这个函数里面有等待"

```python
# 普通函数
def chat(self, user_message):
    ...

# 异步函数——在 def 前面加 async
async def chat(self, user_message):
    ...
```

`async def` 告诉 Python：**这个函数内部会有需要等待的操作**（比如网络请求），Python 要用特殊方式来运行它，使得等待期间可以去做别的事。


#### await：标记"这一步要等"

```python
async def chat(self, user_message):
    # await = "这一步要等 API 返回，等待期间 Python 可以去处理别的事"
    response = await self.client.messages.create(...)
```

`await` 标记的是**具体的等待点**——"到这里我要等网络响应了，Python 你先去忙别的，数据回来了再叫我"。

类比：`async def` 是你声明"我会用异步"，`await` 是你做了具体的等待操。


#### AsyncAnthropic：异步版的客户端

```python
# 同步客户端——它的方法是普通函数，不能 await
client = anthropic.Anthropic(...)
response = client.messages.create(...)       # 普通调用，卡住等

# 异步客户端——它的方法是异步函数，必须 await
client = anthropic.AsyncAnthropic(...)
response = await client.messages.create(...)  # 异步调用，等待期间不卡
```

`Anthropic` 和 `AsyncAnthropic` 功能完全一样，区别只是：同步版的方法是普通函数，异步版的方法是 `async` 函数。要用 `await`，就必须换成异步版。

#### asyncio.run()：启动异步世界的入口

`async def` 函数不能直接调用，需要一个"启动器"：

```python
# ❌ 错误：不能直接调用 async 函数
chat("hello")  # 这不会执行函数，只会返回一个"协程对象"

# ✅ 正确：用 asyncio.run() 启动
asyncio.run(chat("hello"))  # 这才会真正执行
```

`asyncio.run()` 是同步世界和异步世界之间的桥梁——**整个程序只需要在最外层调一次**，进入异步世界后，内部的 `async def` 函数之间可以互相 `await`。

#### 总结：Ch3 → Ch4 全部改动一览

| 之前（Ch3） | 之后（Ch4） | 改了什么 |
|---|---|---|
| `client = anthropic.Anthropic(...)` | `self.client = anthropic.AsyncAnthropic(...)` | async 改造：换异步客户端 |
| `def chat():` | `async def chat():` | async 改造：声明"里面有等待" |
| `response = client.messages.create(...)` | `response = await self.client.messages.create(...)` | async 改造：标记等待点 |
| `chat("hello")` | `asyncio.run(chat("hello"))` | async 改造：启动异步世界的入口 |
| `context = []` | `self.messages = []` | 类重构：状态归属于 Agent 实例，改名与 API 一致 |
| 无 | `self.total_input_tokens += ...` | 新功能：追踪 token 用量 |
| 无 | `self._auto_save()` | 新功能：每轮对话结束后自动保存 |

**重要**：`await` 只能写在 `async def` 函数里面，不能写在裸的顶层代码里——这就是为什么 `chat` 要加 `async`，而且最外层需要 `asyncio.run()` 来启动。

### 整体结构

```python
import anthropic
import asyncio
import time
import uuid

from tools import tool_definitions, execute_tool
from prompt import build_system_prompt
from session import save_session
from ui import (print_assistant_text, print_tool_call, print_tool_result,
                print_error, print_info)


class Agent:
    def __init__(self, *, api_key, base_url=None, model="claude-sonnet-4.6"):
        self.model = model
        self.client = anthropic.AsyncAnthropic(
            base_url=base_url, api_key=api_key,
        )
        self.messages = []
        self.system_prompt = build_system_prompt()
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        # uuid4() 生成一个全局唯一的随机 ID，hex[:8] 取前 8 位十六进制字符
        self.session_id = uuid.uuid4().hex[:8]
        self.session_start_time = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
        )

    async def chat(self, user_message):
        self.messages.append({
            "role": "user",
            "content": [{"type": "text", "text": user_message}],
        })
        # 内层循环：处理工具调用
        while True:
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=self.system_prompt,
                messages=self.messages,
                tools=tool_definitions,
            )
            # 累加 token 用量
            self.total_input_tokens += response.usage.input_tokens
            self.total_output_tokens += response.usage.output_tokens

            assistant_content = []
            tool_results = []
            for block in response.content:
                if block.type == "text":
                    print_assistant_text("\n" + block.text)
                    assistant_content.append({
                        "type": "text", "text": block.text,
                    })
                elif block.type == "tool_use":
                    assistant_content.append({
                        "type": "tool_use", "id": block.id,
                        "name": block.name, "input": block.input,
                    })
                    print_tool_call(block.name, block.input)
                    result = execute_tool(block.name, block.input)
                    print_tool_result(block.name, result)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

            self.messages.append({
                "role": "assistant", "content": assistant_content,
            })
            if tool_results:
                self.messages.append({
                    "role": "user", "content": tool_results,
                })
            else:
                break

        self._auto_save()

    def clear_history(self):
        self.messages = []
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        print_info("Conversation cleared.")

    def show_cost(self):
        total_in = self.total_input_tokens
        total_out = self.total_output_tokens
        print_info(
            f"Tokens: {total_in} in / {total_out} out"
        )

    def restore_session(self, data):
        messages = data.get("messages")
        if not messages:
            return
        self.messages = messages
        metadata = data.get("metadata", {})
        # 恢复 session_id，让后续 _auto_save() 写回同一个文件
        if metadata.get("id"):
            self.session_id = metadata["id"]
        if metadata.get("startTime"):
            self.session_start_time = metadata["startTime"]
        # 恢复 token 计数，让 /cost 显示累计用量
        self.total_input_tokens = metadata.get("totalInputTokens", 0)
        self.total_output_tokens = metadata.get("totalOutputTokens", 0)
        print_info(
            f"Session restored ({len(self.messages)} messages)."
        )

    def _auto_save(self):
        try:
            save_session(self.session_id, {
                "metadata": {
                    "id": self.session_id,
                    "model": self.model,
                    "startTime": self.session_start_time,
                    "messageCount": len(self.messages),
                    "totalInputTokens": self.total_input_tokens,
                    "totalOutputTokens": self.total_output_tokens,
                },
                "messages": self.messages,
            })
        except Exception:
            # 保存失败不能让对话崩溃
            pass
```

### `def __init__(self, *, ...)` 里的 `*` 是什么

`*` 后面的参数必须用关键字传递，不能按位置：

```python
# 没有 * → 位置参数，容易传错顺序
Agent("sk-xxx", "https://...", "gpt-4o")  # 哪个是 key？哪个是 url？

# 有 * → 强制关键字，意图清晰
Agent(api_key="sk-xxx", base_url="https://...", model="gpt-4o")
```

### `_auto_save` 前面的下划线是什么

Python 的约定：`_` 开头的方法是"内部方法"，意思是"只在类内部用，外部不要直接调"。不是语法强制的，是写给读代码的人看的信号。

### 为什么 _auto_save 包在 try/except 里

保存失败的原因可能是磁盘满了、权限不够等。这些都不应该让正在进行的对话崩溃——对话本身是正常的，保存只是锦上添花。

---

## Step 4：REPL + Ctrl+C 处理

### 什么是 REPL

REPL 是四个单词的缩写：

- **R**ead — 读取用户输入
- **E**val — 执行/处理
- **P**rint — 打印结果
- **L**oop — 循环，回到第一步

你用过的 Python 交互式命令行就是一个 REPL：

```
>>> 1 + 1    ← Read（读你输入的）
2            ← Eval + Print（算出来，打印）
>>>          ← Loop（等你下一条输入）
```

我们的 mini claude 也需要这样一个循环：用户打字 → agent 回答 → 等下一条输入 → 一直转，直到用户退出。`run_repl` 就是启动这个循环的函数。

> 文件：`agent.py`（在底部添加）

在 `Agent` 类下面加 REPL 循环和入口函数：

```python
import signal
import sys
from ui import print_welcome, print_user_prompt
from session import load_session, get_latest_session_id


async def run_repl(agent):
    sigint_count = 0

    def handle_sigint(sig, frame):
        nonlocal sigint_count
        sigint_count += 1
        if sigint_count >= 2:
            print("\nBye!\n")
            sys.exit(0)
        print("\n  Press Ctrl+C again to exit.")
        print_user_prompt()

    # 注册信号处理函数：收到 SIGINT（Ctrl+C）时调用 handle_sigint
    signal.signal(signal.SIGINT, handle_sigint)
    print_welcome()

    while True:
        print_user_prompt()
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!\n")
            break

        inp = line.strip()
        sigint_count = 0

        if not inp:
            continue
        if inp in ("exit", "quit"):
            print("\nBye!\n")
            break

        # REPL 命令
        if inp == "/clear":
            agent.clear_history()
            continue
        if inp == "/cost":
            agent.show_cost()
            continue

        try:
            await agent.chat(inp)
        except Exception as e:
            print_error(str(e))


def main():
    import os
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print_error("DEEPSEEK_API_KEY not set")
        sys.exit(1)

    agent = Agent(
        api_key=api_key,
        base_url="https://api.deepseek.com/anthropic",
    )

    # --resume 支持（简化版：直接恢复最近一次会话）
    # 真正的 Claude Code 会列出多个历史会话让用户选择，这里先只取最新的
    if "--resume" in sys.argv:
        session_id = get_latest_session_id()
        if session_id:
            session = load_session(session_id)
            if session:
                agent.restore_session(session)
        else:
            print_info("No previous sessions found.")

    # 检查是否有直接传入的 prompt（单次模式）
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    prompt = " ".join(args) if args else None

    if prompt:
        asyncio.run(agent.chat(prompt))
    else:
        asyncio.run(run_repl(agent))


if __name__ == "__main__":
    main()
```

### Ctrl+C 的双重语义

```text
                    ┌─ 第一次 → 提醒 "Press Ctrl+C again to exit."
用户按 Ctrl+C ─────┤
                    └─ 第二次 → 退出程序
```

为什么不是按一次就退出？因为用户可能手滑按到 Ctrl+C，一次就退出会丢失整个对话。两次确认是安全网。

### nonlocal 是什么

`handle_sigint` 是 `run_repl` 里面的嵌套函数。它要修改外层函数的 `sigint_count` 变量，必须用 `nonlocal` 声明，否则 Python 会把它当成一个新的局部变量。

类比：你在自己房间（内层函数）里想修改客厅（外层函数）的温度，得先声明"我要改的是客厅那个空调（nonlocal），不是我房间的"。

### signal.signal(signal.SIGINT, handle_sigint)

`SIGINT` 是操作系统发给程序的"中断信号"，按 Ctrl+C 时触发。`signal.signal()` 注册一个函数来处理这个信号，替代默认行为（默认是直接退出程序）。

### 两种运行模式

```bash
# REPL 模式：不传参数，进入交互循环
python agent.py

# 单次模式：传参数，执行后退出
python agent.py "读取 README.md 的内容"
```

Claude Code 也是这样：`claude` 进入 REPL，`claude "fix bug"` 单次执行。

---

## Step 5：测试

```bash
python agent.py
```

**测试 1：基本对话和工具显示**

你应该看到彩色的欢迎信息，工具调用时有图标和颜色。

**测试 2：REPL 命令**

```text
> /cost
  ℹ Tokens: 1234 in / 567 out

> /clear
  ℹ Conversation cleared.
```

**测试 3：Ctrl+C 安全**

按一次 Ctrl+C，应该看到提示而不是崩溃。再按一次才退出。

**测试 4：会话恢复**

```bash
# 先正常对话几轮，然后退出
python agent.py
> 你好
> exit

# 用 --resume 恢复
python agent.py --resume
```

应该看到 "Session restored (N messages)." 的提示。

**测试 5：单次模式**

```bash
python agent.py "列出当前目录的文件"
```

应该执行完直接退出，不进入 REPL。

---

## 回头看：从脚本到工具

本章做了一个质变——从"能跑的脚本"变成"能用的工具"：

```text
之前（Ch3 结束时）                  之后（Ch4 结束时）
┌──────────────┐                   ┌──────────────┐
│  agent.py    │ while 循环 + 散装  │  agent.py    │ Agent 类 + async + REPL
│  tools.py    │ 工具定义 + 执行    │  tools.py    │ 工具定义 + 执行（不变）
│  prompt.py   │ system prompt     │  prompt.py   │ system prompt（不变）
│              │      →            │  ui.py       │ 终端输出格式化（新增）
│              │                   │  session.py  │ 会话持久化（新增）
└──────────────┘                   └──────────────┘
```

三个关键升级：
1. **async/await**：从同步阻塞变成异步，为 Ch5 流式输出做好准备
2. **Agent 类**：状态集中管理，方法各司其职
3. **会话持久化**：对话不再是用完即丢的

---

## 完整参考代码

### ui.py

```python
import sys
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console(highlight=False, soft_wrap=True)

ACCENT = "bright_cyan"
MUTED = "dim"
WARNING = "yellow"
ERROR = "red"


def print_welcome():
    title = Text()
    title.append("Mini Claude Code", style=f"bold {ACCENT}")
    title.append("\nfrom scratch", style=MUTED)

    body = Text()
    body.append("Type your request, or ", style=MUTED)
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


def print_user_prompt():
    console.print(f"\n[bold {ACCENT}]╭─[/bold {ACCENT}] [dim]you[/dim]")
    console.print(f"[bold {ACCENT}]╰─>[/bold {ACCENT}] ", end="")


def print_assistant_text(text):
    sys.stdout.write(text)
    sys.stdout.flush()


def print_tool_call(name, inp):
    icon = TOOL_ICONS.get(name, "◆")
    summary = _get_tool_summary(name, inp)
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


def print_tool_result(name, result):
    result = str(result)
    max_len = 500
    if len(result) > max_len:
        result = result[:max_len] + f"\n  ... ({len(result)} chars total)"
    console.print(
        Panel(
            result,
            title=f"[dim]{name} result[/dim]",
            title_align="left",
            border_style="dim",
            padding=(0, 1),
        )
    )


def print_error(msg):
    console.print()
    console.print(
        Panel(
            str(msg),
            title=f"[bold {ERROR}]Error[/bold {ERROR}]",
            title_align="left",
            border_style=ERROR,
            padding=(0, 1),
        )
    )


def print_info(msg):
    console.print(f"\n[bold {ACCENT}]•[/bold {ACCENT}] [dim]{msg}[/dim]")


TOOL_ICONS = {
    "read_file": "📖",
    "write_file": "✏️",
    "edit_file": "🔧",
    "list_files": "📁",
    "grep_search": "🔍",
    "run_shell": "💻",
}


def _get_tool_summary(name, inp):
    if name in ("read_file", "write_file", "edit_file"):
        return inp.get("file_path", "")
    if name == "list_files":
        return inp.get("path", ".")
    if name == "grep_search":
        return f'"{inp.get("pattern", "")}" in {inp.get("path", ".")}'
    if name == "run_shell":
        cmd = inp.get("command", "")
        return cmd[:60] + "..." if len(cmd) > 60 else cmd
    return ""
```

### session.py

```python
import json
from pathlib import Path

SESSION_DIR = Path.home() / ".mini-claude" / "sessions"


def save_session(session_id, data):
    """存档：把会话数据写成 JSON 文件"""
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    path = SESSION_DIR / f"{session_id}.json"
    path.write_text(json.dumps(data, indent=2, default=str))


def load_session(session_id):
    """读档：按 ID 读回某次会话"""
    path = SESSION_DIR / f"{session_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def list_sessions():
    """列出存档：扫描所有会话的元信息"""
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for f in SESSION_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            if "metadata" in data:
                results.append(data["metadata"])
        except Exception:
            pass
    return results


def get_latest_session_id():
    """取最新：找到最近一次会话的 ID"""
    sessions = list_sessions()
    if not sessions:
        return None
    sessions.sort(key=lambda s: s.get("startTime", ""), reverse=True)
    return sessions[0].get("id")
```

### agent.py

```python
import anthropic
import asyncio
import os
import signal
import sys
import time
import uuid

from tools import tool_definitions, execute_tool
from prompt import build_system_prompt
from session import save_session, load_session, get_latest_session_id
from ui import (print_welcome, print_user_prompt, print_assistant_text,
                print_tool_call, print_tool_result, print_error, print_info)


class Agent:
    def __init__(self, *, api_key, base_url=None, model="claude-sonnet-4.6"):
        self.model = model
        self.client = anthropic.AsyncAnthropic(
            base_url=base_url, api_key=api_key,
        )
        self.messages = []
        self.system_prompt = build_system_prompt()
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.session_id = uuid.uuid4().hex[:8]
        self.session_start_time = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
        )

    async def chat(self, user_message):
        self.messages.append({
            "role": "user",
            "content": [{"type": "text", "text": user_message}],
        })
        while True:
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=self.system_prompt,
                messages=self.messages,
                tools=tool_definitions,
            )
            self.total_input_tokens += response.usage.input_tokens
            self.total_output_tokens += response.usage.output_tokens

            assistant_content = []
            tool_results = []
            for block in response.content:
                if block.type == "text":
                    print_assistant_text("\n" + block.text)
                    assistant_content.append({
                        "type": "text", "text": block.text,
                    })
                elif block.type == "tool_use":
                    assistant_content.append({
                        "type": "tool_use", "id": block.id,
                        "name": block.name, "input": block.input,
                    })
                    print_tool_call(block.name, block.input)
                    result = execute_tool(block.name, block.input)
                    print_tool_result(block.name, result)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

            self.messages.append({
                "role": "assistant", "content": assistant_content,
            })
            if tool_results:
                self.messages.append({
                    "role": "user", "content": tool_results,
                })
            else:
                break

        self._auto_save()

    def clear_history(self):
        self.messages = []
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        print_info("Conversation cleared.")

    def show_cost(self):
        print_info(
            f"Tokens: {self.total_input_tokens} in / "
            f"{self.total_output_tokens} out"
        )

    def restore_session(self, data):
        messages = data.get("messages")
        if not messages:
            return
        self.messages = messages
        metadata = data.get("metadata", {})
        # 恢复 session_id，让后续 _auto_save() 写回同一个文件
        if metadata.get("id"):
            self.session_id = metadata["id"]
        if metadata.get("startTime"):
            self.session_start_time = metadata["startTime"]
        # 恢复 token 计数，让 /cost 显示累计用量
        self.total_input_tokens = metadata.get("totalInputTokens", 0)
        self.total_output_tokens = metadata.get("totalOutputTokens", 0)
        print_info(
            f"Session restored ({len(self.messages)} messages)."
        )

    def _auto_save(self):
        try:
            save_session(self.session_id, {
                "metadata": {
                    "id": self.session_id,
                    "model": self.model,
                    "startTime": self.session_start_time,
                    "messageCount": len(self.messages),
                    "totalInputTokens": self.total_input_tokens,
                    "totalOutputTokens": self.total_output_tokens,
                },
                "messages": self.messages,
            })
        except Exception:
            pass


async def run_repl(agent):
    sigint_count = 0

    def handle_sigint(sig, frame):
        nonlocal sigint_count
        sigint_count += 1
        if sigint_count >= 2:
            print("\nBye!\n")
            sys.exit(0)
        print("\n  Press Ctrl+C again to exit.")
        print_user_prompt()

    signal.signal(signal.SIGINT, handle_sigint)
    print_welcome()

    while True:
        print_user_prompt()
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!\n")
            break

        inp = line.strip()
        sigint_count = 0

        if not inp:
            continue
        if inp in ("exit", "quit"):
            print("\nBye!\n")
            break

        if inp == "/clear":
            agent.clear_history()
            continue
        if inp == "/cost":
            agent.show_cost()
            continue

        try:
            await agent.chat(inp)
        except Exception as e:
            print_error(str(e))


def main():
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print_error("DEEPSEEK_API_KEY not set")
        sys.exit(1)

    agent = Agent(
        api_key=api_key,
        base_url="https://api.deepseek.com/anthropic",
    )

    # --resume 支持（简化版：直接恢复最近一次会话）
    # 真正的 Claude Code 会列出多个历史会话让用户选择，这里先只取最新的
    if "--resume" in sys.argv:
        session_id = get_latest_session_id()
        if session_id:
            session = load_session(session_id)
            if session:
                agent.restore_session(session)
        else:
            print_info("No previous sessions found.")

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    prompt = " ".join(args) if args else None

    if prompt:
        asyncio.run(agent.chat(prompt))
    else:
        asyncio.run(run_repl(agent))


if __name__ == "__main__":
    main()
```

---

## 暂时省略的能力

| 省略内容 | 原因 | 补回章节 |
|---|---|---|
| `argparse` 参数解析 | 当前只需 `--resume`，`sys.argv` 足够 | Ch6（权限章引入 `--yolo`、`--model` 后升级） |
| 工具调用时的颜色 diff 显示 | `edit_file` / `write_file` 的增删行着色 | Ch6（权限）或独立改进 |
| Spinner 等待动画 | 流式输出后等待感消失 | Ch5（Streaming） |
| REPL 命令 `/compact` `/plan` `/memory` `/skills` | 依赖后续章节的功能 | Ch7-Ch10 各章补入 |
| 处理中按 Ctrl+C 中断当前请求 | 需要 `agent.abort()` + `asyncio.Task` 取消机制 | Ch5（Streaming） |
| `--yolo` `--plan` 等权限模式参数 | 权限系统尚未实现 | Ch6（Permissions） |
| `--model` 参数 + resume 时有条件恢复 model | 当前 model 写死，无需判断。Claude Code 的逻辑是"用户指定了用用户的，没指定就恢复上次的" | Ch6（引入 `argparse` 后一起加） |

---

## 进阶阅读

### 原版 vs 入门版对比

| 维度 | 原版（参考实现） | 入门版 |
|---|---|---|
| 参数解析 | `argparse` 11 个参数 | `sys.argv` 手动检查 |
| 运行模式 | 单次 + REPL + `--resume` | 单次 + REPL + `--resume` |
| Ctrl+C | 处理中中断 + 空闲双击退出 | 仅空闲双击退出 |
| 会话格式 | 双后端（Anthropic/OpenAI）分开存 | 单后端统一存 |
| REPL 命令 | `/clear` `/cost` `/compact` `/plan` `/memory` `/skills` | `/clear` `/cost` |
| UI | `rich` 彩色 + spinner + diff 着色 | `rich` Panel 基础版 |
| 文件拆分 | `__main__.py` + `agent.py` + `session.py` + `ui.py` | `agent.py`（含 REPL）+ `session.py` + `ui.py` |

### 可观察的自主性

Claude Code UX 的核心理念：**Agent 自由行动，但让用户实时看到每一步**。

```text
📖 read_file src/app.ts
  1 | import express from ...
  ... (1234 chars total)

✏️ edit_file src/app.ts
  - const port = 3000
  + const port = process.env.PORT
```

中断成本远低于撤销成本。用户在 Agent 走错方向前几秒就能按 Ctrl+C，而不是等它执行完再花更多时间撤销。这就是为什么每个工具调用都要实时显示名称、参数和结果。

### JSONL vs JSON

原版文档提到 Claude Code 用 JSONL（每行一条 JSON）而不是整体 JSON 来存会话。好处是：

- **追加写入**：每轮对话只 append 一行，O(1) 写入；整体 JSON 要覆盖写整个文件
- **崩溃安全**：崩溃最多丢最后一行；整体 JSON 写到一半崩了整个文件损坏
- **对话越长越明显**：100 轮对话时，JSONL 还是 O(1)，JSON 每次写入量线性增长

我们用整体 JSON 是因为简单，几十轮对话完全够用。如果未来对话很长，可以考虑切换到 JSONL。
