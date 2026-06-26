# 5. 流式输出 — 让回答逐字显示

## 本章目标

目前你的 Agent 调用 API 后要等**整个回答生成完**才一次性显示出来。回答越长，等待越久——用户面对空白屏幕的容忍极限大约 2-3 秒。

流式输出让第一个字在几百毫秒内就出现，把"等 30 秒"变成"看着内容逐渐写出来"。

| 维度 | 之前（非流式） | 之后（流式） |
|---|---|---|
| 首字延迟 | 等全部生成完（5-30 秒） | 几百毫秒 |
| 用户体验 | 空白等待，不知道在干什么 | 看到文字逐渐出现 |
| 中断能力 | 只能等结束 | 发现方向错了立刻 Ctrl+C |
| 等待动画 | 无 | Spinner 转圈 → 流式文本接替 |

本章会做三件事：

1. 加一个 **Spinner 等待动画**，让用户知道 Agent 在思考
2. 把 API 调用从 **非流式** 改成 **流式**，文本逐字输出
3. 加入 **重试机制**，网络错误或服务过载时自动重试

参考代码：

- [`agent.py`](../examples/chapter-05/agent.py)
- [`tools.py`](../examples/chapter-05/tools.py)
- [`prompt.py`](../examples/chapter-05/prompt.py)
- [`session.py`](../examples/chapter-05/session.py)
- [`ui.py`](../examples/chapter-05/ui.py)

### 暂时省略的能力

| 省略内容 | 原因 | 补回章节 |
|---|---|---|
| 流式工具执行（streaming tool execution） | 需要理解 `content_block_stop` 事件 + `asyncio.Task` | 进阶阅读已介绍概念，实现留待后续 |
| 并行工具执行（`asyncio.gather`） | 需要先有权限系统区分安全/危险工具 | Ch6（Permissions）后可实现 |
| Extended Thinking（扩展思考） | DeepSeek 有自己的思考模式，不走 Anthropic thinking API | 使用 Anthropic 原生 API 时可选加入 |
| OpenAI 兼容后端流式 | 当前只用 Anthropic SDK | 双后端支持时补回 |

---

## Step 1：加入 Spinner — 等待动画

> 文件：`ui.py`（添加）

在 API 调用期间显示一个转圈动画，让用户知道 Agent 在思考，而不是死机了。

```python
import threading
import time


SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

_spinner_thread = None
_spinner_stop = threading.Event()


def start_spinner(label="Thinking"):
    global _spinner_thread
    if _spinner_thread is not None:
        return
    _spinner_stop.clear()

    def _run():
        frame = 0
        sys.stdout.write(f"\n  {SPINNER_FRAMES[0]} {label}...")
        sys.stdout.flush()
        while not _spinner_stop.is_set():
            time.sleep(0.08)
            frame = (frame + 1) % len(SPINNER_FRAMES)
            # \r 回到行首，覆盖上一帧
            sys.stdout.write(f"\r  {SPINNER_FRAMES[frame]} {label}...")
            sys.stdout.flush()

    _spinner_thread = threading.Thread(target=_run, daemon=True)
    _spinner_thread.start()


def stop_spinner():
    global _spinner_thread
    if _spinner_thread is None:
        return
    _spinner_stop.set()
    _spinner_thread.join(timeout=1)
    _spinner_thread = None
    # \r\033[K = 回到行首 + 清除整行
    sys.stdout.write("\r\033[K")
    sys.stdout.flush()
```

### 为什么用 threading 而不是 asyncio

Spinner 需要每 80ms 刷新一帧，但主线程在 `await` 等 API 响应——如果用 asyncio，`await` 期间没有机会执行 spinner 的代码。所以开一个独立的后台线程来做动画。

`daemon=True` 意思是：这个线程在主程序退出时自动销毁，不会阻止程序退出。

### threading.Event() 是什么

一个线程间的开关。`_spinner_stop.set()` 把开关拨到"停"，spinner 线程里 `_spinner_stop.is_set()` 检测到后退出循环。比直接用全局变量更安全。

### `\r\033[K` 是什么

这是终端控制序列（ANSI escape code）：
- `\r` 把光标移到行首
- `\033[K` 清除从光标到行尾的内容

效果就是把 spinner 那一行擦干净，为后面的流式文本让位。

---

## Step 2：理解流式 API 的工作原理

### 我们现在的问题

我们现在调 API 用的是 `client.messages.create()`。这是**非流式**调用：你发一个请求，服务器**生成完所有内容**后，一次性把整个回答返回给你。

### 解法：流式调用

Anthropic SDK 提供了另一个方法 `client.messages.stream()`，用来做**流式**调用：服务器**边生成边推送**，每生成几个 token 就立刻推一小段给你。

代码上只需要把 `.create()` 换成 `.stream()` 就行了。但 `.stream()` 返回的不是一个完整的 response，而是一条**流**（stream）——你需要从这条流里逐个接收数据。

### stream 和 event：水管和水滴

这是理解流式代码的关键。

**`stream` 是一条水管，`event` 是水管里流过来的一滴滴水。**

调用 `.stream()` 就是打开了一条从服务器到你电脑的连接（水管）。水管本身不是内容，它只是一个通道。

服务器每生成一小段内容，就往水管里推一滴水（一个 event）。比如服务器在生成"你好，我是助手"时：

```text
event 1: "你好"       ← 第一滴水
event 2: "，我是"     ← 第二滴水
event 3: "助手"       ← 第三滴水
```

你的代码要做的就是：**从水管里一滴一滴地接水，接到文本就立即打印到终端。**

用代码表示这两层关系：

```python
async with ... as stream:          # 打开水管（连接）
    async for event in stream:     # 从水管里一滴滴接水（事件）
        # 这滴水里有文本吗？有就立即打印
```

两个新语法的作用：
- `async with` — 确保水管最后一定会关闭（类比 `with open("file") as f` 确保文件关闭）
- `async for` — 从水管里逐个取水滴，没有新水滴时自动等待，有了就继续

水管里的水滴流完后，调用 `stream.get_final_message()` 可以拿到完整的响应对象——格式和非流式的 `response` 完全一样，所以工具调用那部分代码不用改。

流结束后（水管里没有水了），调用 `stream.get_final_message()` 拿到完整的响应对象——格式和非流式的 `response` 完全一样，工具调用的处理逻辑不用改。

---

## Step 3：改造 chat 方法 + 提取 _call_stream

> 文件：`agent.py`

现在把上面的思路落地。改动分两部分：

**第一部分：提取 `_call_stream` 方法**

把流式 API 调用从 `chat` 里独立出来，让 `chat` 保持清晰。在 Agent 类里加一个新方法：

```python
async def _call_stream(self):
    async with self.client.messages.stream(
        model=self.model,
        max_tokens=8192,
        system=self.system_prompt,
        messages=self.messages,
        tools=tool_definitions,
    ) as stream:
        first_text = True
        async for event in stream:                          # 逐个接收事件
            if hasattr(event, "type") and event.type == "content_block_delta":
                delta = event.delta
                if hasattr(delta, "text"):                  # 是文本增量
                    if first_text:
                        stop_spinner()                      # 第一段文本到达，停掉等待动画
                        print_assistant_text("\n")
                        first_text = False
                    print_assistant_text(delta.text)         # 立即输出到终端

        return await stream.get_final_message()             # 流结束，拿到完整响应
```

`first_text` 的作用：第一段文本到达时做两件事——停掉 spinner、输出一个换行。之后的文本直接追加输出，不需要再做这些。

**第二部分：修改 `chat` 方法**

把原来的 `self.client.messages.create(...)` 替换成 `self._call_stream()`，并且去掉 text 分支里的 `print_assistant_text()`（因为流式过程中已经逐字输出过了）：

```python
async def chat(self, user_message):
    self.messages.append({
        "role": "user",
        "content": [{"type": "text", "text": user_message}],
    })
    while True:
        start_spinner()                                     # API 调用前启动等待动画
        response = await self._call_stream()                # 流式调用（文本在里面已经逐字输出）
        stop_spinner()                                      # 兜底：万一流里没有文本，也要停动画

        self.total_input_tokens += response.usage.input_tokens
        self.total_output_tokens += response.usage.output_tokens

        assistant_content = []
        tool_results = []
        for block in response.content:
            if block.type == "text":
                assistant_content.append({                  # 只存历史，不再打印（流式已输出）
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
```

和 Ch4 的 `chat` 对比，只有两处变化：
1. `self.client.messages.create(...)` → `self._call_stream()`
2. text 分支去掉了 `print_assistant_text()`（流式已输出）

其余的工具处理、消息历史、内层循环逻辑完全不变。

---

## Step 4：重试机制

> 文件：`agent.py`（添加两个独立函数，然后修改 `_call_stream`）

### 问题：网络请求会失败

API 请求不是每次都成功——网络抖动、服务过载都会导致报错。但有些错误是**临时的**（等一会儿就好了），有些是**永久的**（等再久也没用）。我们的策略：

- 临时错误 → 自动重试几次
- 永久错误 → 直接报错，让用户去修

### 第一步：判断"该不该重试"

先写一个函数，接收错误对象，返回 True（该重试）或 False（别重试）：

```python
def _is_retryable(error):
    status = getattr(error, "status_code", None)  # 安全取属性，没有就返回 None
    if status in (429, 503, 529):
        return True
    if "overloaded" in str(error):
        return True
    return False
```

这些状态码的含义：

| 状态码 | 含义 | 重试？ |
|---|---|---|
| 429 | 请求太频繁（限流） | 是，等一会儿再试 |
| 503 | 服务暂时不可用 | 是 |
| 529 | 服务过载 | 是 |
| 400 | 请求参数错误 | 否，代码有 bug |
| 401 | API Key 无效 | 否，配置问题 |

> `getattr(error, "status_code", None)` 是什么？安全地取对象的属性。直接写 `error.status_code`，如果 error 没有这个属性会报 AttributeError；用 `getattr` 没有就返回 `None`，不会报错。

### 第二步：写通用重试函数

重试的逻辑和"重试什么"是分开的——不管是流式请求还是普通请求，重试的套路都一样：试一次 → 失败了判断要不要再试 → 等一会儿 → 再试。

所以我们把"重试套路"提取成一个通用函数。它接收一个参数 `fn`——**`fn` 就是"你想重试的那个操作"**，是一个函数。Python 里函数可以当参数传给另一个函数，就像把一个任务交给别人去执行：

```python
# 类比：你把"打电话给 API"这件事交给重试函数
# 重试函数负责：试一次，失败了等一会儿再试
_with_retry(打电话给API)
```

完整实现：

```python
async def _with_retry(fn, max_retries=3):
    import random
    for attempt in range(max_retries + 1):  # 最多试 max_retries + 1 次（含第一次）
        try:
            return await fn()              # 执行传进来的操作
        except Exception as error:
            if attempt >= max_retries or not _is_retryable(error):
                raise                      # 次数用完或不可重试 → 抛出错误
            delay = min(2 ** attempt, 30) + random.random()
            print_info(f"Retry {attempt + 1}/{max_retries} in {delay:.1f}s...")
            await asyncio.sleep(delay)     # 等一会儿再试
```

执行流程举例（假设前两次失败，第三次成功）：

```
attempt=0 → fn() 报错 429 → 可重试 → 等 1.x 秒
attempt=1 → fn() 报错 503 → 可重试 → 等 2.x 秒
attempt=2 → fn() 成功 → return 结果
```

> **为什么用指数退避（1s → 2s → 4s）而不是固定间隔？** 服务过载时，如果 100 个客户端都固定 1 秒后重试，会同时涌入形成"重试风暴"，让服务更加过载。每次翻倍 + 随机抖动（`random.random()`）能把重试分散开。

### 第三步：把 `_call_stream` 包上重试

现在把 Step 2 写的 `_call_stream` 改造一下——把原来的流式请求逻辑包进一个内部函数 `_do`，然后交给 `_with_retry` 去执行：

```python
async def _call_stream(self):
    async def _do():                       # 把流式请求包成一个函数
        async with self.client.messages.stream(
            model=self.model,
            max_tokens=8192,
            system=self.system_prompt,
            messages=self.messages,
            tools=tool_definitions,
        ) as stream:
            first_text = True
            async for event in stream:
                if hasattr(event, "type") and event.type == "content_block_delta":
                    delta = event.delta
                    if hasattr(delta, "text"):
                        if first_text:
                            stop_spinner()
                            print_assistant_text("\n")
                            first_text = False
                        print_assistant_text(delta.text)

            return await stream.get_final_message()

    return await _with_retry(_do)          # 交给重试函数：失败了自动重试
```

关键变化只有两处：
1. 原来的代码被包进了 `async def _do()` 里
2. 最后一行从"直接执行"变成"交给 `_with_retry` 去执行"

`_with_retry(_do)` 里，`_do` 就是传进去的 `fn`。当 `_with_retry` 内部执行 `await fn()` 时，实际执行的就是 `_do()`——也就是那段流式请求代码。

---

## Step 5：测试

```bash
python agent.py
```

**测试 1：Spinner 和流式输出**

输入任何问题，你应该看到：
1. Spinner 转圈动画（⠋ ⠙ ⠹ ...）
2. 第一个字到达时 spinner 消失
3. 文字逐字逐句出现，不是一次性蹦出来

**测试 2：工具调用的显示**

```text
> 读取 agent.py 的内容
  ⠹ Thinking...              ← spinner
                              ← spinner 消失，流式文本开始
  我来读取 agent.py 的内容。
  📖 read_file agent.py       ← 工具调用（带图标）
  import anthropic...          ← 工具结果（灰色，截断）
  
  文件内容如上...              ← 后续回复也是流式的
```

**测试 3：重试（可选）**

临时把 API key 改成无效值，应该看到重试信息。但 401 错误不应该重试（不可恢复）。

---

## 回头看：流式改了什么

```text
之前：
  用户输入 → [等 10 秒] → 一次性显示全部文本

之后：
  用户输入 → [spinner 0.5 秒] → 文字逐字出现... → 完成
```

核心改动只有一个地方：`client.messages.create()` → `client.messages.stream()`。SDK 负责 SSE 解析，你只需要在事件回调里把文本写到终端。

---

## 完整参考代码

### ui.py（在 Ch4 基础上增加 spinner）

```python
import sys
import threading
import time
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


SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

_spinner_thread = None
_spinner_stop = threading.Event()


def start_spinner(label="Thinking"):
    global _spinner_thread
    if _spinner_thread is not None:
        return
    _spinner_stop.clear()

    def _run():
        frame = 0
        sys.stdout.write(f"\n  {SPINNER_FRAMES[0]} {label}...")
        sys.stdout.flush()
        while not _spinner_stop.is_set():
            time.sleep(0.08)
            frame = (frame + 1) % len(SPINNER_FRAMES)
            sys.stdout.write(f"\r  {SPINNER_FRAMES[frame]} {label}...")
            sys.stdout.flush()

    _spinner_thread = threading.Thread(target=_run, daemon=True)
    _spinner_thread.start()


def stop_spinner():
    global _spinner_thread
    if _spinner_thread is None:
        return
    _spinner_stop.set()
    _spinner_thread.join(timeout=1)
    _spinner_thread = None
    sys.stdout.write("\r\033[K")
    sys.stdout.flush()
```

### agent.py（完整版，含流式 + 重试）

```python
import anthropic
import asyncio
import os
import random
import signal
import sys
import time
import uuid

from tools import tool_definitions, execute_tool
from prompt import build_system_prompt
from session import save_session, load_session, get_latest_session_id
from ui import (print_welcome, print_user_prompt, print_assistant_text,
                print_tool_call, print_tool_result, print_error, print_info,
                start_spinner, stop_spinner)


def _is_retryable(error):
    status = getattr(error, "status_code", None)
    if status in (429, 503, 529):
        return True
    if "overloaded" in str(error):
        return True
    return False


async def _with_retry(fn, max_retries=3):
    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except Exception as error:
            if attempt >= max_retries or not _is_retryable(error):
                raise
            delay = min(2 ** attempt, 30) + random.random()
            print_info(f"Retry {attempt + 1}/{max_retries} in {delay:.1f}s...")
            await asyncio.sleep(delay)


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
            start_spinner()                                     # API 调用前启动等待动画
            response = await self._call_stream()                # 流式调用（文本已逐字输出）
            stop_spinner()                                      # 兜底停动画

            self.total_input_tokens += response.usage.input_tokens
            self.total_output_tokens += response.usage.output_tokens

            assistant_content = []
            tool_results = []
            for block in response.content:
                if block.type == "text":
                    assistant_content.append({                  # 只存历史，不再打印
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

    async def _call_stream(self):
        async def _do():
            async with self.client.messages.stream(             # 打开流式连接
                model=self.model,
                max_tokens=8192,
                system=self.system_prompt,
                messages=self.messages,
                tools=tool_definitions,
            ) as stream:
                first_text = True
                async for event in stream:                      # 逐个接收事件
                    if (hasattr(event, "type")
                            and event.type == "content_block_delta"):
                        delta = event.delta
                        if hasattr(delta, "text"):              # 是文本增量
                            if first_text:
                                stop_spinner()                  # 第一段文本到达，停掉动画
                                print_assistant_text("\n")
                                first_text = False
                            print_assistant_text(delta.text)    # 立即输出

                return await stream.get_final_message()         # 流结束，拿完整响应

        return await _with_retry(_do)

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
        if messages:
            self.messages = messages
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
        print("Error: DEEPSEEK_API_KEY not set")
        sys.exit(1)

    agent = Agent(
        api_key=api_key,
        base_url="https://api.deepseek.com/anthropic",
    )

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

## 进阶阅读

### 流式工具执行（Streaming Tool Execution）

我们当前的实现是：等整个 API 响应结束 → 遍历所有工具调用 → 逐个执行。

参考实现做了一个优化：在流式接收过程中，当一个 `tool_use` 块**刚完成**（`content_block_stop` 事件），如果它是安全的只读工具（`read_file`、`list_files`、`grep_search`），就**立即开始执行**——不等整个响应结束。

```text
当前（串行）：
  [===== API 流式响应 =====] → [工具1] → [工具2] → [工具3]

优化后（流式提前执行）：
  [===== API 流式响应 =====]
       ↑ 工具1完成，立即执行  ↑ 工具2完成
       [工具1 ████]          [工具2 ████]
                                          → [工具3]
```

工具执行时间被"藏"进了 API 响应的流式窗口。文件读取通常 < 100ms，在 5-30 秒的流式窗口内完全可以覆盖。

这需要 `asyncio.create_task()` 来创建并发任务，以及用字典跟踪哪些工具已经提前执行。我们在后续章节有了并发基础后可以实现。

### 并行工具执行

当模型一次返回多个工具调用时，只读工具可以并行执行：

```python
import asyncio

SAFE_TOOLS = {"read_file", "list_files", "grep_search"}

# 把安全工具分组并行执行
safe_tasks = []
for tool in tool_uses:
    if tool.name in SAFE_TOOLS:
        safe_tasks.append(execute_tool(tool.name, tool.input))

results = await asyncio.gather(*safe_tasks)
```

`asyncio.gather()` 同时启动所有任务，等它们全部完成。当模型一次读 3-5 个文件时，并行执行通常带来 2-3 倍的速度提升。

### 重试的边界

不是所有错误都值得重试。参考实现的判断标准：

- **可重试**（临时性错误）：429（限流）、503（暂时不可用）、529（过载）、网络超时
- **不可重试**（永久性错误）：400（参数错误）、401（认证失败）、404（资源不存在）

重试永久性错误只会浪费时间。正确做法是让它们直接抛出，显示错误信息让用户去修复配置。

### 原版 vs 入门版对比

| 维度 | 原版（参考实现） | 入门版 |
|---|---|---|
| 流式输出 | Anthropic + OpenAI 双后端 | 仅 Anthropic |
| 流式工具执行 | `content_block_stop` + `asyncio.Task` | 暂不实现 |
| 并行工具执行 | 安全工具 `asyncio.gather` / 流式提前执行 | 暂不实现 |
| 重试 | 指数退避 + 随机抖动 + abort signal | 指数退避 + 随机抖动 |
| Extended Thinking | adaptive / enabled / disabled 三模式 | 暂不实现 |
| Spinner | 后台线程动画 | 后台线程动画 |

---

# 可选增强：UI Polish — Markdown 渲染和更稳的输入框

> **这部分可做可不做。** 做了改善的是日常使用体验，不做不影响 agent 核心能力。如果你想先推进 Ch6 Permissions，可以跳过这里，以后再回来。

## 它解决什么问题

到这里，你的 agent 已经能流式输出、自动重试了。但真正用起来会碰到两个烦人的体验问题：

| 问题 | 你会遇到什么 | 根本原因 |
|---|---|---|
| **长输入删不动** | 输入一长串文字后按 Backspace，光标位置错乱、删不干净 | Python 内置 `input()` 不知道前面 Rich prompt 的真实宽度，换行后重绘出错 |
| **Markdown 显示不好看** | 模型回复里的标题、列表、代码块全是原始 `#`、`-`、` ``` ` 符号 | `sys.stdout.write()` 只是原样打印文本，不做任何渲染 |

做了这部分之后：
- 长输入可以正常删除、光标移动，还能用方向键翻历史记录
- 非流式场景下的完整回复会渲染成漂亮的 Markdown（标题、列表、代码高亮）

### 不做的话呢

功能上完全不受影响。agent loop、工具调用、streaming、重试——这些核心能力都不依赖 UI polish。只是用起来稍微粗糙一点。

---

## 暂时省略的高级 UI 能力

| 省略内容 | 为什么省略 | 对齐的原版能力 |
|---|---|---|
| 流式 Markdown 渲染 | 需要组件状态、增量解析和重绘策略，超出入门版范围 | Claude Code 的 React/Ink 渲染层 |
| Vim 模式 / 快捷键自定义 | 需要 key bindings 设计，和 agent loop 关系不大 | Claude Code 的键盘系统 |
| 多 Tab / 多面板 / 鼠标交互 | 需要完整 TUI 框架 | Claude Code 的组件化终端 UI |

---

## UI-Step 1：安装 prompt_toolkit

> 文件：`requirements.txt`

`rich` 已经有了。现在加一个输入框库：

```text
prompt_toolkit
```

然后安装：

```bash
pip install -r requirements.txt
```

### prompt_toolkit 解决什么

Python 内置的 `input()` 很简单，但不适合复杂终端 UI：

- 长文本自动换行后，删除和光标移动容易显示错位
- 没有历史记录
- 不知道 Rich 画出来的 prompt 占了多少宽度

`prompt_toolkit` 是专门做终端输入框的。它会接管光标、换行、删除、历史记录这些细节。

---

## UI-Step 2：在 ui.py 里创建输入框

> 文件：`ui.py`

先增加这些 import：

```python
from pathlib import Path
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style
```

然后在 `ui.py` 里添加：

```python
PROMPT_STYLE = Style.from_dict({
    "prompt": "ansibrightcyan bold",
    "muted": "ansibrightblack",
})


def create_prompt_session():
    history_dir = Path.home() / ".mini-claude"
    history_dir.mkdir(parents=True, exist_ok=True)
    history_file = history_dir / "history.txt"
    return PromptSession(history=FileHistory(str(history_file)))


def prompt_user(session):
    message = [
        ("class:prompt", "mini"),
        ("class:muted", " > "),
    ]
    return session.prompt(message, style=PROMPT_STYLE)
```

关键点：
- `FileHistory` 会把你输入过的内容写进文件，下次启动还能用方向键找回来
- `session.prompt()` 返回的还是普通字符串，后面的 agent 逻辑不用变

---

## UI-Step 3：在 run_repl() 里替换 input()

> 文件：`agent.py`

先更新导入，加上 `create_prompt_session` 和 `prompt_user`。

然后把 `run_repl()` 里的输入部分改掉。

之前：

```python
async def run_repl(agent):
    sigint_count = 0

    print_welcome()

    while True:
        print_user_prompt()
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!\n")
            break
```

之后：

```python
async def run_repl(agent):
    sigint_count = 0
    prompt_session = create_prompt_session()

    print_welcome()

    while True:
        try:
            line = prompt_user(prompt_session)
        except (EOFError, KeyboardInterrupt):
            print("\nBye!\n")
            break
```

用了 `prompt_toolkit` 之后，`print_user_prompt()` 不再需要——输入提示符由 `prompt_user()` 统一负责。

### 为什么这能解决"长输入删不动"

原来是两段拼出来的：`print_user_prompt()` 画提示符，`input()` 读输入。但 `input()` 不知道前面那个 Rich prompt 占了多少宽度，长输入换行后重绘就会错位。

新的方式里，提示符和输入都由 `prompt_toolkit` 管，它知道光标在哪、文本多长、换行后怎么重绘。

---

## UI-Step 4：Markdown 渲染完整回复

> 文件：`ui.py`

增加 import：

```python
from rich.markdown import Markdown
```

然后添加一个新函数：

```python
def print_assistant_markdown(text):
    console.print()
    console.print(Markdown(text))
```

### 什么时候用、什么时候不用

**能用**：非流式版本（Ch4），`block.text` 已经是一整段完整回复，可以直接：

```python
print_assistant_markdown(block.text)
```

**不能用**：Ch5 流式版本里的 `delta.text` 只是片段，不是完整 Markdown。比如模型最终想输出一个代码块，流式片段可能是 `` ```py ``、`thon`、`print`——前几个片段根本不是合法的完整代码块，每次都渲染会导致终端闪烁和格式错乱。

所以流式版本继续用 `print_assistant_text(delta.text)` 原样打印。

---

## UI-Step 5：测试

### 测试 1：长输入

启动后输入一长段文字，按方向键、Backspace、左右移动光标，观察是否比 `input()` 稳定。

### 测试 2：历史记录

输入两句话后退出，再重新启动，按上方向键，看能不能找到之前输入过的话。

### 测试 3：Markdown（仅非流式版本）

让模型输出 Markdown，看标题、列表和代码块是否有更清楚的渲染效果。如果你在 Ch5 流式版本里测试，看到的是原样 Markdown，这是正常的。

---

## 完整改动清单

| 文件 | 改什么 |
|---|---|
| `requirements.txt` | 加 `prompt_toolkit` |
| `ui.py` | 加 `create_prompt_session()`、`prompt_user()`、`print_assistant_markdown()` |
| `agent.py` | 在 `run_repl()` 里用 `prompt_user(prompt_session)` 替代 `print_user_prompt()` + `input()` |

---

## 原版 vs 入门版对比

| 维度 | 原版 Claude Code | 本章入门增强 |
|---|---|---|
| 输入框 | React/Ink + 自定义键盘系统 | `prompt_toolkit` |
| 长输入编辑 | 稳定 | 稳定很多 |
| 历史记录 | 完整命令历史 | `FileHistory` |
| Markdown | 组件级流式渲染 | 完整文本渲染（仅非流式场景） |
| Streaming | 边接收边更新 UI | 原样打印 delta |
| 多行输入 | 支持复杂快捷键 | 暂不默认开启 |
