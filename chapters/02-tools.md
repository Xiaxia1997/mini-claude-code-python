# 2. 工具系统 — 让模型从"只会说话"变成"能做事"

本章参考代码：[examples/chapter-02/agent.py](../examples/chapter-02/agent.py) · [examples/chapter-02/tools.py](../examples/chapter-02/tools.py)

## 本章目标

上一章的程序已经可以多轮聊天，但它还不能真正执行任务。

这一章先实现 `read_file`，跑通一次完整的工具调用：

```text
用户提出任务
    ↓
模型决定调用工具
    ↓
agent.py 接收 tool_use
    ↓
tools.py 真正执行工具
    ↓
agent.py 把 tool_result 返回给模型
    ↓
模型根据结果继续回答或继续调用工具
```

本章结束时，你可以输入：

```text
读一下 agent.py，然后告诉我它做了什么
```

模型会真的读取文件，再根据文件内容回答。

理解这个闭环后，可以用相同结构继续加入：

- `read_file`
- `write_file`
- `edit_file`
- `list_files`
- `grep_search`
- `run_shell`

工具数量会增加，但整体架构保持不变：

```text
tool_definitions + execute_tool + 两层 Agent Loop
```

---

## 文件分工

这一章会同时修改两个文件。先记住这条边界：

| 文件 | 负责什么 | 不负责什么 |
|---|---|---|
| `tools.py` | 定义工具、真正执行工具、返回执行结果 | 不调用大模型、不维护聊天记录 |
| `agent.py` | 调用大模型、识别 `tool_use`、调用 `execute_tool`、返回 `tool_result` | 不直接写具体的文件读取逻辑 |


---

# 第一部分：先只跑通 `read_file`

## Step 1：在 `tools.py` 中定义工具

> 文件：`tools.py`

工具定义是给模型看的"说明书"：

```python
read_file_tool = {
    "name": "read_file",
    "description": "读取文件内容，返回文本",
    "input_schema": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "要读取的文件路径"
            }
        },
        "required": ["file_path"]
    }
}
```

这段代码不会读取任何文件，它只告诉模型：

- 有一个叫 `read_file` 的工具；
- 它可以读取文件；
- 调用时必须提供 `file_path`。

### 为什么放在 `tools.py`

因为它描述的是"工具具有什么能力"，属于工具箱。

它不应该放在 `agent.py`。以后工具变成 6 个、10 个时，如果全塞进 `agent.py`，Agent Loop 会越来越难读。

### Anthropic 和 OpenAI 的工具格式区别

这里使用 Anthropic API 的工具格式：

```python
{
    "name": "read_file",
    "description": "...",
    "input_schema": {...}
}
```

OpenAI 格式通常会多一层：

```python
{
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "...",
        "parameters": {...}
    }
}
```
---

## Step 2：完成真正读取文件的函数

> 文件：`tools.py`

```python
def read_file(file_path):
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            return file.read()
    except Exception as e:
        return f"Error: {e}"
```

### 逐行理解

```python
with open(file_path, "r", encoding="utf-8") as file:
```

- `file_path`：要读取的文件路径；
- `"r"`：read，只读模式；
- `encoding="utf-8"`：按 UTF-8 读取，避免中文乱码；
- `with`：代码块结束后自动关闭文件。

```python
return file.read()
```

读取完整文件并把内容返回。

```python
except Exception as e:
    return f"Error: {e}"
```

如果文件不存在、没有权限或编码错误，不让整个 Agent 崩溃，而是把错误转换成字符串。

例如：

```text
Error: [Errno 2] No such file or directory: 'abc.py'
```

模型看到这个结果后，可以换一个路径再次尝试。

---

## Step 3：先单独测试 `read_file`

先不要急着接大模型。在包含 `tools.py` 和 `agent.py` 的目录中运行：

```bash
python -c 'from tools import read_file; print(read_file("agent.py")[:200])'
```

如果能看到 `agent.py` 的前 200 个字符，说明工具执行函数已经正常。

如果这里失败，问题在 `tools.py`，还没有进入 Agent Loop。分开测试更容易定位错误。

---

## Step 4：在 `tools.py` 中集中导出工具定义

> 文件：`tools.py`

在文件底部加入：

```python
tool_definitions = [read_file_tool]
```

以后增加工具，只需要继续追加：

```python
tool_definitions = [
    read_file_tool,
    write_file_tool,
    edit_file_tool,
]
```

这里放的是工具定义，不是执行结果。

---

## Step 5：在 `agent.py` 中把工具告诉模型

> 文件：`agent.py`

先显式导入，方便看清每个名字从哪里来：

```python
from tools import tool_definitions, read_file
```

然后在 API 请求中加入：

```python
response = client.messages.create(
    model="claude-sonnet-4.6",
    max_tokens=8192,
    system="You are a helpful assistant",
    messages=context,
    tools=tool_definitions,
)
```

### 这一步做了什么

`tools=tool_definitions` 只是把工具说明书交给模型。

它不会自动运行 `read_file()`。

模型如果认为需要读取文件，会在响应中返回一个 `tool_use` block：

```text
ToolUseBlock(
    id="toolu_abc123",
    name="read_file",
    input={"file_path": "agent.py"},
    type="tool_use"
)
```

模型只是在说：

```text
请宿主程序帮我调用 read_file("agent.py")
```

真正执行它的仍然是宿主程序中的 Python 代码。

---

## Step 6：理解加了工具后的 response 完整结构

加了 `tools` 参数后，response 比上一章多了两个关键东西。一棵树看全：

```
response
  ├── content（列表，模型返回了什么）
  │     ├── [0] ThinkingBlock              ← 思考过程（DeepSeek 默认开启）
  │     │     ├── type: "thinking"
  │     │     └── thinking: "用户想读文件，我应该用 read_file..."
  │     ├── [1] TextBlock                  ← 模型说的话（可能有，也可能没有）
  │     │     ├── type: "text"
  │     │     └── text: "让我读一下这个文件"
  │     └── [2] ToolUseBlock               ← 工具调用请求
  │           ├── type: "tool_use"
  │           ├── id: "toolu_abc123"       ← 这次调用的唯一 ID（喂回结果时必须带上）
  │           ├── name: "read_file"        ← 要调哪个工具
  │           └── input: {"file_path": "agent.py"}   ← 参数（已经是字典）
  │
  ├── stop_reason（字符串，模型为什么停下来了）
  │     ├── "end_turn"    → 说完了，没有更多操作
  │     ├── "tool_use"    → 想调工具，等你执行完再继续
  │     └── "max_tokens"  → 输出太长被截断
  │
  └── usage
        ├── input_tokens: 12
        └── output_tokens: 25
```

**两个维度别搞混**：
- **content** 回答的是"模型说了什么"——可以同时包含文字和工具调用
- **stop_reason** 回答的是"模型为什么停了"——决定你的代码接下来怎么做

一次响应可以**同时有 TextBlock + ToolUseBlock** 在 content 里（模型先说一句话，再调工具），此时 stop_reason 是 `"tool_use"`。

### 各种 block 怎么处理

| block.type | 是什么 | 你的代码该怎么做 |
|---|---|---|
| `thinking` | 模型内部思考 | 跳过，不打印也不保存 |
| `text` | 模型说的话 | 打印 + 保存到历史 |
| `tool_use` | 工具调用请求 | 保存到历史 + 执行工具 |

### 循环判断

遍历完所有 block 后，有没有 `tool_use` 类型的 block 自然决定了下一步：

- **有 tool_use** → 把工具结果喂回模型，继续内层循环
- **没有 tool_use** → 模型说完了，退出内层循环

具体怎么遍历 block、怎么构造历史消息，都在 Step 8 的完整代码里。

先看 Step 7，了解 `execute_tool` 是什么。

---

## Step 7：在 `tools.py` 中加入分发器 + 结果截断

> 文件：`tools.py`

模型返回 `block.name = "read_file"` 和 `block.input = {"file_path": "agent.py"}`，你的代码需要把这两个信息转换成真正的 Python 函数调用。

这个"按名字找函数并执行"的逻辑叫**分发器**，放在 `tools.py`：

```python
MAX_RESULT_LENGTH = 50000  # 50K 字符上限


def _truncate_result(result):
    """超长结果保留头尾，中间截断"""
    if len(result) <= MAX_RESULT_LENGTH:
        return result
    half = MAX_RESULT_LENGTH // 2
    return (
        result[:half]
        + f"\n\n... ({len(result)} chars total, truncated) ...\n\n"
        + result[-half:]
    )


def execute_tool(name, args):
    if name == "read_file":
        return _truncate_result(read_file(args["file_path"]))

    return f"Unknown tool: {name}"
```

### 为什么要截断工具结果

工具返回的结果会原样塞进消息历史发给模型。如果 `read_file` 读了一个 500KB 的文件，这 500K 文本全部进入上下文窗口，后面的对话就放不下了。

`_truncate_result` 保留头尾各 25K 字符，中间用省略号连接——头部有文件开头（通常是 import 和类定义），尾部有文件结尾（通常是 main 函数），对模型理解文件结构够用了。

调用过程：

```text
execute_tool("read_file", {"file_path": "agent.py"})
    ↓
read_file("agent.py")
    ↓
_truncate_result(文件内容)
    ↓
返回（可能截断的）文件内容
```

### 为什么不把 if/elif 写在 `agent.py` 里

如果以后有 6 个工具，`agent.py` 会变成：

```python
if name == "read_file":
    ...
elif name == "write_file":
    ...
elif name == "edit_file":
    ...
elif name == "run_shell":
    ...
```

这些都是工具分发逻辑，属于 `tools.py`。`agent.py` 只需要一句：

```python
result = execute_tool(block.name, block.input)
```

边界清楚了：

- `agent.py` 只知道"模型想调用某个工具"；
- `tools.py` 决定"这个工具具体怎么执行"。

以后增加新工具时，`agent.py` 不需要改任何代码。

### 修改 `agent.py` 的导入

> 文件：`agent.py`

```python
from tools import tool_definitions, execute_tool
```

---

## Step 8：内层循环——处理响应 + 连续工具调用

模型可能读完一个文件后继续读另一个，或者先搜索再读取再修改。所以需要**两层循环**：

```text
外层 while：等待用户输入
    内层 while：模型调用工具 → 执行 → 喂回结果 → 再次调模型，直到任务完成
```

内层循环每一轮做三件事：

1. 遍历 `response.content`，处理所有 block（打印 text、保存 text + tool_use、执行工具）
2. 把 assistant 消息追加到 `context`
3. 看 `stop_reason` 决定：继续循环还是退出

> 文件：`agent.py`

```python
while True:
    user_input = input("> ")
    if user_input == "exit":
        break

    context.append({"role": "user", "content": user_input})

    # --- 内层循环：模型可能连续调用多个工具 ---
    while True:
        response = client.messages.create(
            model="claude-sonnet-4.6",
            max_tokens=8192,
            system="You are a helpful assistant",
            messages=context,
            tools=tool_definitions,
        )

        # --- 遍历 response.content，按 block.type 分拣处理 ---
        assistant_content = []
        tool_results = []

        for block in response.content:
            # thinking → 跳过

            if block.type == "text":
                print(block.text)
                assistant_content.append({
                    "type": "text",
                    "text": block.text,
                })

            elif block.type == "tool_use":
                assistant_content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })
                result = execute_tool(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })

        # --- 追加历史 + 判断是否继续 ---
        context.append({"role": "assistant", "content": assistant_content})

        if tool_results:
            # 有工具被调用 → 把结果喂回模型，继续内层循环
            context.append({"role": "user", "content": tool_results})
        else:
            # 没有工具调用 → 模型说完了，退出内层循环
            break
```

整段代码**只用了一个判断维度：`block.type`**。遍历时按 type 分拣，遍历完后 `tool_results` 是否为空自然决定了循环行为——不需要再单独看 `stop_reason`。

### 几个容易出错的点

**消息顺序**：必须先 assistant 再 user，顺序反了 API 会报错。

**`tool_use_id` 配对**：一次响应可能同时调用多个工具（`toolu_001`、`toolu_002`），`tool_result` 的 `tool_use_id` 必须和对应的 `block.id` 一一对应。

**工具结果的角色是 `user`**：不是说内容来自真人，而是 Anthropic 用 `user` 角色表示"外部环境返回给模型的信息"。

---

## Step 9：补充工作目录 + 测试

模型调用 `read_file("tools.py")` 时，Python 的 `open("tools.py")` 从**当前工作目录**开始找文件。为避免路径问题导致read_file失败

在 `agent.py` 开头加一行，让工作目录自动切到脚本所在目录：

```python
import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
```

- `__file__`：当前脚本的文件路径
- `os.path.dirname(...)`：取目录部分
- `os.chdir(...)`：切换工作目录

这样不管从哪里启动，`open("tools.py")` 都能找到同目录下的文件。

运行测试：

```bash
python agent.py
```

依次试这三个：

```text
> 读一下 tools.py
> 读一下abc.py，并告诉我发生了什么（abc.py其实不存在）
> 先读 tools.py，再读 agent.py，然后解释它们怎么配合
```

第三个测试验证内层循环能否处理连续工具调用。

---

# 接下来：自己补充其他工具

`read_file` 跑通后，`agent.py` 已经不用再改了。接下来只动 `tools.py`，每个新工具三步：

1. 写工具定义（给模型看的说明书）
2. 写执行函数（真正干活的 Python 代码）
3. 在 `execute_tool` 里注册

模式和 `read_file` 完全一样，试试自己加 `list_files` 或 `write_file`。

### 六个核心工具

| 工具 | 做什么 | 核心 Python API |
|---|---|---|
| `read_file` | 读文件 | `open(..., "r")` — 已完成 |
| `list_files` | 列出目录内容 | `os.listdir()` |
| `write_file` | 写文件 | `open(..., "w")` |
| `edit_file` | 精确替换文件中的字符串 | `str.replace()` |
| `grep_search` | 搜索文件内容 | `subprocess.run(["grep", ...])` |
| `run_shell` | 执行任意命令 | `subprocess.run()` |

建议按上面的顺序逐个加入：先只读（`list_files`），再写入（`write_file`、`edit_file`），最后是更通用的（`grep_search`、`run_shell`）。

### `**args` 技巧：工具多了之后升级分发器

只有 1 个工具时，手动取参数：

```python
return read_file(args["file_path"])
```

工具多了以后，可以用 `**args` 自动展开字典为函数参数：

```python
handler(**args)
# 等价于 read_file(file_path="agent.py")
```

前提：**工具定义中的参数名必须和 Python 函数的参数名一致**。

---

### 完整参考代码

自己先写，卡住了再看。

#### tools.py（完整文件）

```python
import os
import subprocess


# ── 工具定义（给模型看的说明书）──

read_file_tool = {
    "name": "read_file",
    "description": "读取文件内容",
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
    "description": "写入文件内容，文件不存在会创建，已存在会覆盖",
    "input_schema": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "要写入的文件路径",
            },
            "content": {
                "type": "string",
                "description": "要写入的完整内容",
            },
        },
        "required": ["file_path", "content"],
    },
}

edit_file_tool = {
    "name": "edit_file",
    "description": "精确替换文件中的一段字符串，old_string 必须在文件中唯一存在",
    "input_schema": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "要编辑的文件路径",
            },
            "old_string": {
                "type": "string",
                "description": "要被替换的原始字符串（必须唯一）",
            },
            "new_string": {
                "type": "string",
                "description": "替换后的新字符串",
            },
        },
        "required": ["file_path", "old_string", "new_string"],
    },
}

grep_search_tool = {
    "name": "grep_search",
    "description": "在文件中搜索匹配的文本，返回匹配行及行号",
    "input_schema": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "要搜索的文本或正则表达式",
            },
            "path": {
                "type": "string",
                "description": "要搜索的目录路径，默认当前目录",
            },
        },
        "required": ["pattern"],
    },
}

run_shell_tool = {
    "name": "run_shell",
    "description": "执行 shell 命令，返回 stdout 和 stderr",
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的 shell 命令",
            },
        },
        "required": ["command"],
    },
}


# ── 执行函数（真正干活的代码）──

def read_file(file_path):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Error: {e}"


def list_files(path="."):
    try:
        entries = os.listdir(path)
        return "\n".join(sorted(entries))
    except Exception as e:
        return f"Error: {e}"


def write_file(file_path, content):
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully wrote to {file_path}"
    except Exception as e:
        return f"Error: {e}"


def edit_file(file_path, old_string, new_string):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        if old_string not in content:
            return f"Error: old_string not found in {file_path}"
        count = content.count(old_string)
        if count > 1:
            return f"Error: old_string found {count} times, must be unique"
        new_content = content.replace(old_string, new_string, 1)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        return f"Successfully edited {file_path}"
    except Exception as e:
        return f"Error: {e}"


def grep_search(pattern, path="."):
    try:
        result = subprocess.run(
            ["grep", "-rn", "--color=never", pattern, path],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 1:
            return "No matches found."
        return result.stdout[:5000] or "No matches found."
    except Exception as e:
        return f"Error: {e}"


def run_shell(command):
    try:
        result = subprocess.run(
            command, shell=True,
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return f"Exit code {result.returncode}\n{result.stderr}"
        return result.stdout or "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: command timed out"
    except Exception as e:
        return f"Error: {e}"


# ── 分发器 + 结果截断 ──

MAX_RESULT_LENGTH = 50000


def _truncate_result(result):
    if len(result) <= MAX_RESULT_LENGTH:
        return result
    half = MAX_RESULT_LENGTH // 2
    return (
        result[:half]
        + f"\n\n... ({len(result)} chars total, truncated) ...\n\n"
        + result[-half:]
    )


def execute_tool(name, args):
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
        return _truncate_result(handler(**args))
    except Exception as e:
        return f"Error: {e}"


tool_definitions = [
    read_file_tool,
    list_files_tool,
    write_file_tool,
    edit_file_tool,
    grep_search_tool,
    run_shell_tool,
]
```

#### agent.py（完整文件）

```python
import anthropic
import os
from tools import tool_definitions, execute_tool

os.chdir(os.path.dirname(os.path.abspath(__file__)))

client = anthropic.Anthropic(
    base_url=os.environ.get("ANTHROPIC_BASE_URL"),
    api_key=os.environ["DEEPSEEK_API_KEY"],
)
context = []

while True:
    user_input = input("> ")
    if user_input == "exit":
        break

    context.append({"role": "user", "content": [{"type": "text", "text": user_input}]})

    while True:
        response = client.messages.create(
            model="claude-sonnet-4.6",
            max_tokens=8192,
            system="You are a helpful assistant",
            messages=context,
            tools=tool_definitions,
        )

        assistant_content = []
        tool_results = []

        for block in response.content:
            if block.type == "text":
                print(block.text)
                assistant_content.append({
                    "type": "text",
                    "text": block.text,
                })
            elif block.type == "tool_use":
                assistant_content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })
                result = execute_tool(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })

        context.append({"role": "assistant", "content": assistant_content})

        if tool_results:
            context.append({"role": "user", "content": tool_results})
        else:
            break
```

---

## 本章完成检查

- [ ] `tools.py`：工具定义 + 执行函数 + `execute_tool` 分发器
- [ ] `agent.py`：外层循环（用户输入）+ 内层循环（工具链）
- [ ] assistant 历史中同时保留了 text 和 tool_use
- [ ] 工具结果用 `tool_result` + `tool_use_id` 返回
- [ ] 输入"读一下 agent.py"能得到基于真实文件内容的回答

---

## 本章暂时省略的能力

| 省略了什么 | 为什么省略 | 哪一章补回 | 补回后对齐参考实现的哪个能力 |
|---|---|---|---|
| read-before-edit / mtime 保护 | `edit_file` 当前只做唯一性检查。参考实现还会记录文件的读取时间（mtime），编辑前检查文件是否被外部修改过，防止覆盖他人改动 | Ch6 Permissions | `tools.py` 的 `readFileState` + mtime 检测 |
| 引号规范化（curly quote tolerance） | 模型有时会输出中文引号 `""` 而不是 ASCII 直引号 `""`，导致 `old_string` 匹配不到。参考实现有 `_find_actual_string` 做容错 | 可作为 Ch2 进阶练习 | `tools.py` 的 `_find_actual_string()` |
| WebFetch 工具 | 网页抓取 + HTML 剥离，核心工具链不依赖它 | 按需补入 | `tools.py` 的 `web_fetch()` |
| ToolSearch / 延迟加载 | 工具少时不需要延迟加载机制 | Ch9 Skills 或按需 | `tools.py` 的 deferred tools 机制 |

---

> **下一章**：工具定义了 Agent 的能力，System Prompt 定义了 Agent 应该怎样使用这些能力。
