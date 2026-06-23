# 2. 工具系统 — 让模型从“只会说话”变成“能做事”

## 本章目标

上一章我们做了一个多轮聊天机器人。但聊天机器人不是 Agent——Agent 的核心区别是**能做事**：读文件、写文件、跑命令。

本章结束时：你的程序能接受指令“读一下 agent.py”，模型会自己决定调用 `read_file` 工具，你的代码执行读取，把文件内容喂回模型，模型再给出总结。

> 本章正在实现中。下面先把完整消息流拆开，再逐步把它写进代码。

---

## 工具到底是什么

工具就是你给模型的一张“菜单”。你告诉它：“你可以读文件、写文件、跑命令”，当它需要做这些事时，它不回复文字，而是返回一个结构化的请求：“请帮我执行 read_file，参数是 file_path=agent.py”。

**模型自己不能执行任何操作**——它只能“点菜”，真正做事的是你的 Python 代码。

整个流程：

```text
你：“读一下 agent.py 这个文件”
    ↓
模型看到自己有 read_file 工具可用
    ↓
模型返回：我要调用 read_file，参数 file_path="agent.py"
    ↓
你的代码：真正去读文件，拿到内容
    ↓
你把文件内容喂回给模型
    ↓
模型：“这个文件是一个 Python 脚本，它做了...”
```

## Step 1：定义一个工具

Anthropic API 的工具定义格式：

```python
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
```

和 OpenAI 格式的区别：

- 没有外层的 `"type": "function"` 和 `"function": {...}` 包装
- 参数字段叫 `input_schema`（不是 `parameters`）
- 更扁平，少一层嵌套

三个关键字段：

- **name**：工具名字，模型调用时会用这个名字
- **description**：告诉模型这个工具干什么，模型根据这个决定什么时候用
- **input_schema**：工具需要什么参数。这是 JSON Schema 格式，`"type": "object"` 表示参数整体是一个字典，`properties` 里列出每个参数的名字和类型，`required` 标记哪些必填

建议新建一个 `tools.py` 文件，把工具定义放在里面，和 `agent.py` 分开。

## Step 2：把工具传给 API

在 `agent.py` 的 API 调用里加一个 `tools` 参数：

```python
tools = [read_file_tool]  # 可以传多个工具

response = client.messages.create(
    model="claude-sonnet-4.6",
    max_tokens=4096,
    system="你是一个有帮助的助手",
    messages=messages,
    tools=tools,        # 告诉模型它有什么工具可用
)
```

加了这行之后，模型的回复就可能不是纯文字了，而是一个工具调用请求。

## Step 3：理解 Anthropic 的响应格式

Anthropic 的 `response.content` 是一个**列表**，里面每个元素都有 `type` 字段：

```text
response.content = [
    TextBlock(type="text", text="让我读一下这个文件"),
    ToolUseBlock(
        type="tool_use",
        id="toolu_abc123",
        name="read_file",
        input={"file_path": "agent.py"},
    ),
]
```

可能的 block 类型：

| type | 含义 | 什么时候出现 |
|---|---|---|
| `text` | 模型说的话 | 几乎每次都有 |
| `tool_use` | 模型要调用工具 | 模型决定用工具时 |
| `thinking` | 模型的思考过程 | 开启思考模式时 |

一次响应里可以**同时有多种 block**。比如模型可能先说一句话（text），然后调用工具（tool_use）。

关键判断——看 `stop_reason`：

```python
if response.stop_reason == "tool_use":
    # 模型要调用工具 → 去执行
    ...
else:
    # 模型说完了（stop_reason == "end_turn"）→ 打印文字
    for block in response.content:
        if block.type == "text":
            print(block.text)
```

**这就是 Agent Loop 的核心分支**——上一章的 while 循环只有“打印文字 → 继续”，现在变成了“有工具调用 → 执行工具 → 喂回结果 → 继续循环”。

## Step 4：执行工具

模型说“我要读 agent.py”，你的代码就真的去读：

```python
for block in response.content:
    if block.type == "tool_use":
        name = block.name
        args = block.input        # 已经是字典了，不需要 json.loads()

        if name == "read_file":
            try:
                with open(args["file_path"], "r", encoding="utf-8") as file:
                    result = file.read()
            except Exception as exc:
                result = f"Error: {exc}"
        else:
            result = f"Unknown tool: {name}"
```

注意：Anthropic 格式的 `block.input` 直接就是字典，不像部分 OpenAI 接口格式那样需要从 JSON 字符串解析。

## Step 5：把工具结果喂回模型

这是最关键的一步。Anthropic API 要求：

1. 先把 assistant 的完整响应推入 messages
2. 再用一条新的 `user` 消息推入工具结果（注意：不是 `tool` 角色）

```python
# 1. 推入 assistant 的完整响应
messages.append({"role": "assistant", "content": response.content})

# 2. 推入工具结果（角色是 "user"，内容是 tool_result 列表）
tool_results = []
for block in response.content:
    if block.type == "tool_use":
        result = execute_tool(block.name, block.input)
        tool_results.append({
            "type": "tool_result",
            "tool_use_id": block.id,       # 关联回哪个工具调用
            "content": result,             # 工具执行的结果
        })

messages.append({"role": "user", "content": tool_results})
```

和 OpenAI 格式的区别：

- OpenAI 常用 `role: "tool"` 角色，每个工具结果是单独一条消息
- Anthropic 用 `role: "user"` 角色，所有工具结果打包在一条消息里的 `content` 列表中
- `tool_use_id` 必须和 `block.id` 对应，API 靠这个关联

推完之后，继续 while 循环，再次调用 API——这次模型会看到文件内容，然后给出总结。

## Step 6：完整的 Agent Loop

把上面所有步骤拼起来，你的 while 循环变成了：

```python
while True:
    user_input = input("> ")
    if user_input == "exit":
        break

    messages.append({"role": "user", "content": user_input})

    # 内层循环：处理工具调用链
    while True:
        response = client.messages.create(
            model="claude-sonnet-4.6",
            max_tokens=4096,
            system="你是一个有帮助的助手",
            messages=messages,
            tools=tools,
        )

        # 把 assistant 响应推入历史
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "tool_use":
            # 模型要调用工具
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = execute_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
            messages.append({"role": "user", "content": tool_results})
            # 工具结果推完，继续内层循环让模型处理结果
        else:
            # 模型回复纯文字（stop_reason == "end_turn"），任务完成
            for block in response.content:
                if block.type == "text":
                    print(block.text)
            break  # 退出内层循环，等待下一次用户输入
```

注意这里有**两层循环**：

- **外层循环**：等用户输入
- **内层循环**：处理工具调用链，直到模型不再调用工具

为什么需要内层循环？因为模型可能连续调用多次工具：先读文件 → 发现需要搜索 → 再读另一个文件 → 最后给出答案。每次工具执行完都要回到模型让它决定下一步。

## 消息数组在工具调用时怎么增长

```text
初始:
  messages = [
    {"role": "user", "content": "读一下 agent.py"},
  ]

模型决定调用工具:
  messages = [
    {"role": "user", "content": "读一下 agent.py"},
    {"role": "assistant", "content": [TextBlock("让我看看"), ToolUseBlock(read_file)]},
    {"role": "user", "content": [{"type": "tool_result", "content": "文件内容..."}]},
  ]

模型看到文件内容后回复:
  messages = [
    {"role": "user", "content": "读一下 agent.py"},
    {"role": "assistant", "content": [TextBlock("让我看看"), ToolUseBlock(read_file)]},
    {"role": "user", "content": [{"type": "tool_result", "content": "文件内容..."}]},
    {"role": "assistant", "content": [TextBlock("这个文件是...")]},
                                       ↑ stop_reason == "end_turn" → 循环结束
  ]
```

注意消息严格交替：user → assistant → user → assistant。工具结果用 `role: "user"` 推入就是为了维持这个交替。

## 本章实现清单

1. **新建 `tools.py`**：定义 `read_file_tool`（字典）并写 `execute_tool` 函数
2. **修改 `agent.py`**：从 `tools.py` 导入，加 `tools=` 参数，加 `stop_reason` 判断和内层循环
3. **验证调用链**：输入“读一下 agent.py”，观察模型是否调用工具并基于文件内容继续回答

先只做 `read_file` 一个工具，跑通之后再加 `write_file` 和 `run_shell`。

---

## 加更多工具

跑通 `read_file` 后，加工具就是重复相同模式。

### write_file

```python
# 定义：name="write_file"，参数 file_path + content
# 执行：open(path, "w").write(content)
```

### run_shell

```python
# 定义：name="run_shell"，参数 command
# 执行：subprocess.run(command, shell=True, capture_output=True)
```

有了这三个工具（读、写、跑命令），你的 agent 就能做大部分基础编程任务了。

## 错误也是结果

一个重要的设计原则：**工具执行失败时，不要让整个 Agent 进程直接崩溃，而是把错误信息作为工具结果返回给模型。**

```python
# ❌ 不要这样
def read_file(path):
    return open(path).read()  # 文件不存在会抛异常，程序崩溃


# ✅ 应该这样
def read_file(path):
    try:
        with open(path, encoding="utf-8") as file:
            return file.read()
    except Exception as exc:
        return f"Error: {exc}"  # 返回错误信息给模型
```

为什么？因为模型拿到错误信息后可以自我纠正：“文件不存在？让我换个路径试试”。如果你的程序直接崩了，模型连纠正的机会都没有。

**错误是 Agent 可以继续观察和处理的数据。**

---

> **下一章**：工具定义了 Agent 的能力，但 system prompt 定义了它的行为——怎么用这些工具、什么时候该小心、当前在哪个目录。

---

## 生产级实现还会做什么（进阶阅读）

> 以下内容用于理解生产级 Coding Agent 的扩展方向，第一遍可以跳过。

### 工具数量差异

教学实现只需要少量核心工具。生产级 Coding Agent 通常还会加入更精细的编辑、搜索、子 Agent、技能和外部协议集成。

### edit_file 为什么常用字符串替换

“查找旧字符串 → 替换为新字符串”相较行号编辑和全文件重写有两个优势：

- **行号编辑**：第一次插入几行后，后续所有行号偏移，多步编辑需要复杂重算
- **全文件重写**：大文件浪费 Token，模型可能遗漏未修改的代码
- **字符串替换**：如果模型给了一个文件中不存在的字符串，工具直接报错，模型可以重新读文件纠正

### 并行工具执行

更成熟的实现可以并行执行互不依赖的只读工具。我们的第一版先串行执行，优先理解消息流。

### 结果截断

工具输出可能很大。直接全部塞进消息数组会迅速消耗上下文窗口，所以需要截断或把大结果写入磁盘，只向模型返回摘要和引用。
