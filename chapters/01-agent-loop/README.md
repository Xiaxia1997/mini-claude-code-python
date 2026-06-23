# 1. Agent Loop — 从零开始，真的从零

## 本章目标

一步步写出一个能多轮对话的 LLM 客户端。这是 Agent Loop 的地基——没有这个，后面的工具、权限、记忆全是空中楼阁。

本章结束时你会有：一个在终端里可以反复跟 LLM 对话、且模型能记住上文的 Python 程序。

---

## Step 0：理解“客户端”是什么

你要调 LLM 的 API（比如 DeepSeek、Anthropic），就像你要打电话给一个人——你需要一部“电话”。**客户端就是这部电话**。

创建客户端本身不会发任何网络请求，它只是保存了两件事：

- **往哪里发**（base_url）
- **用什么身份**（api_key）

真正“打电话”是你调 `client.messages.create(...)` 的时候。

## Step 1：环境准备

### 安装依赖

```bash
pip install anthropic
```

`anthropic` 这个包是 Anthropic 官方 SDK。DeepSeek 兼容 Anthropic 的 API 格式，所以用同一个 SDK 就能调 DeepSeek。

### 设置环境变量

API Key 是你的身份凭证，**绝对不要写在代码里**。把它存到环境变量：

```bash
# 在 ~/.zshrc 末尾加两行
export ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
export ANTHROPIC_API_KEY=your_deepseek_api_key
```

加完后重开终端，或者 `source ~/.zshrc` 让它生效。

两个环境变量的作用：

- `ANTHROPIC_BASE_URL`：告诉 SDK 把请求发到 DeepSeek 而不是 Anthropic
- `ANTHROPIC_API_KEY`：你的身份凭证（DeepSeek 的 API Key）

Anthropic SDK 会自动读这两个环境变量，代码里不需要手动传。但你也可以显式传：

```python
import os

client = anthropic.Anthropic(
    base_url="https://api.deepseek.com/anthropic",
    api_key=os.environ["DEEPSEEK_API_KEY"],
)
```

两种写法效果一样，看你喜欢哪种。

## Step 2：调一次 API

先写一个最小能跑的东西：

```python
import anthropic

client = anthropic.Anthropic()

response = client.messages.create(
    model="claude-sonnet-4.6",          # DeepSeek 会自动映射到 deepseek-v4-flash
    max_tokens=4096,
    system="你是一个有帮助的助手",       # system prompt 是单独的参数
    messages=[
        {"role": "user", "content": "你好"}
    ],
)

# 打印模型的回复（遍历找 text block）
for block in response.content:
    if block.type == "text":
        print(block.text)
```

运行 `python agent.py`，如果终端打印了类似“你好！有什么可以帮助你的？”，恭喜，你成功调通了 LLM API。

### Anthropic 的消息格式

和 OpenAI 格式有几个区别：

- **system prompt 是单独的参数**，不是放在 messages 数组里
- **content 可以是字符串，也可以是列表**。简单文字直接写字符串就行：

  ```python
  {"role": "user", "content": "你好"}           # ✅ 简单写法
  {"role": "user", "content": [{"type": "text", "text": "你好"}]}  # ✅ 完整写法
  ```

### response 的结构

```text
response
  ├── content（列表，里面是一个个 block，每个 block 有 type 字段）
  │     ├── [0] ThinkingBlock  ← 如果开了思考模式，第一个是思考过程
  │     │     ├── type: "thinking"
  │     │     └── thinking: "让我想想...用户说你好..."
  │     └── [1] TextBlock      ← 模型的实际回复
  │           ├── type: "text"
  │           └── text: "你好！有什么可以帮助你的？"
  ├── model: "claude-sonnet-4.6"
  ├── stop_reason: "end_turn"
  └── usage
        ├── input_tokens: 12
        └── output_tokens: 25
```

**关键点**：`response.content` 是一个**列表**，里面每个元素都有 `type` 字段：

| type | 含义 | 索引位置 |
|---|---|---|
| `thinking` | 模型的思考过程（DeepSeek 默认开启） | 通常是 `[0]` |
| `text` | 模型的实际回复 | thinking 后面 |
| `tool_use` | 工具调用（下一章讲） | text 后面 |

**不要用 `response.content[0].text`！** 因为 `[0]` 可能是 thinking block 而不是 text。要遍历找：

```python
for block in response.content:
    if block.type == "text":
        print(block.text)
```

### 模型名字映射

DeepSeek 的 Anthropic API 会自动映射模型名：

| 你写的模型名 | DeepSeek 实际用的 |
|---|---|
| claude-opus-4.6 等 Opus 系列 | deepseek-v4-pro |
| claude-sonnet-4.6、claude-haiku 等 | deepseek-v4-flash |
| 其他不认识的名字 | deepseek-v4-flash |

所以代码里写 `"claude-sonnet-4.6"` 就行，以后换成真正的 Anthropic API 时一行都不用改。

## Step 3：让用户反复输入

一次调用不是 agent，我们需要循环。Python 内置的 `input()` 函数可以等用户输入：

```python
text = input("> ")   # 终端显示 "> "，等你打字，回车后返回字符串
```

把它放进 `while True`：

```python
while True:
    user_input = input("> ")
    if user_input == "exit":
        break
    # 拿着 user_input 去调 API...
```

## Step 4：消息历史——模型怎么“记住”上文

这是理解 Agent Loop 的关键。**模型本身没有记忆**，每次调用都是独立的。那它怎么知道你之前说了什么？

答案：**你把完整的对话历史每次都传给它**。

```python
messages = []  # 放在循环外！

while True:
    user_input = input("> ")
    if user_input == "exit":
        break

    messages.append({"role": "user", "content": user_input})

    response = client.messages.create(
        model="claude-sonnet-4.6",
        max_tokens=4096,
        system="你是一个有帮助的助手",
        messages=messages,
    )

    # 打印：只找 text block
    for block in response.content:
        if block.type == "text":
            print(block.text)

    # 保存：过滤掉 thinking block，只保留 text（后面加工具后还有 tool_use）
    content_to_save = [
        {"type": "text", "text": block.text}
        for block in response.content
        if block.type == "text"
    ]
    messages.append({"role": "assistant", "content": content_to_save})
```

### 为什么要过滤 thinking block

`response.content` 里可能有三种 block：thinking、text、tool_use。

**参考实现的做法是：保存时删掉 thinking block，只保留 text 和 tool_use。**

原因：

- thinking 是模型的内部思考过程，API 不需要它来维持对话连贯性
- 保留它会白白占用上下文窗口（thinking 内容往往很长）
- 参考实现明确过滤了 thinking

```python
# 参考实现的做法：
final_message.content = [
    block for block in final_message.content
    if block.type != "thinking"
]
```

**原则：打印时过滤只显示 text，保存时过滤删掉 thinking。**

### 两个容易犯的错

**错误 1：messages 放在循环里面**

```python
while True:
    messages = []   # ❌ 每轮都重置，模型永远不记得上一轮
```

**错误 2：忘了把模型回复追加到 messages**

```python
for block in response.content:
    if block.type == "text":
        print(block.text)
# ❌ 没有 messages.append(...)，模型下一轮不知道自己说过什么
```

### 消息数组的增长方式

每轮对话，`messages` 数组增长两条：

```text
初始状态:
  messages = []

第 1 轮:
  messages = [
    {"role": "user",      "content": "我叫小明"},
    {"role": "assistant", "content": [{"type": "text", "text": "你好小明！"}]},
  ]
  注意 assistant 的 content 是列表（thinking 已过滤），不是纯字符串

第 2 轮:
  messages = [
    {"role": "user",      "content": "我叫小明"},
    {"role": "assistant", "content": [{"type": "text", "text": "你好小明！"}]},
    {"role": "user",      "content": "我叫什么名字"},
    {"role": "assistant", "content": [{"type": "text", "text": "你叫小明呀！"}]},
  ]
```

模型每次都收到**完整的** messages 列表，所以它能“记住”之前的对话——本质上不是记忆，是你把历史全部重新喂给它了。

这也意味着对话越长，messages 越大，Token 消耗越多。后面会讲怎么压缩。

## 你现在有什么

一个能多轮对话、有记忆的 LLM 客户端。完整代码约 20 行。

**但它只是一个聊天机器人，还不是 Agent。**

Agent 和聊天机器人的区别：**Agent 能做事**。它不只是回复文字，还能读文件、写文件、跑命令。怎么做到的？通过给模型“工具”——这是下一章的内容。

---

> **下一章**：[工具系统](../02-tools/) —— 让模型从“只会说话”变成“能做事”。我们会定义第一个工具 `read_file`，让模型真正读取你电脑上的文件。

---

## 同步 vs 异步：现在不用管

你可能在参考代码里看到 `async def`、`await` 这些关键词。简单解释：

- **同步**（你现在写的）：调 API 的 3 秒里，程序卡住等结果
- **异步**：调 API 的 3 秒里，程序可以去干别的事

你只有一个用户在终端交互，没有“别的事”要干，卡住等着完全合理。**同步版本功能上和异步完全一样**，后面想改随时可以改。先把核心逻辑跑通，不要被语法分心。

## Claude Code 的做法（进阶阅读）

> 以下内容用于理解生产级 Coding Agent 的扩展方向，第一遍可以跳过。

### 双层架构

生产级实现通常会把 Agent Loop 拆成两层：

- **会话级引擎**：管整个对话生命周期——用户输入处理、预算检查、Token 统计、会话恢复
- **单轮查询循环**：管一次查询的执行——消息压缩、API 调用、工具执行、错误恢复

我们把两层合并成一个简单的 while 循环，够用了。

### 循环继续原因

生产级 Coding Agent 的循环除了工具调用，还要处理 Token 截断恢复、错误重试、Hook 拦截等情况。我们先只处理第 1 种：有工具调用就继续，否则退出。

### 为什么会用异步生成器

部分实现会使用异步生成器，主要原因是：

1. **背压控制**：消费端不处理完，生产端不继续
2. **线性控制流**：所有分支用普通 `continue` / `break`，不需要额外状态机

这些都是工程优化，不影响核心逻辑理解。
