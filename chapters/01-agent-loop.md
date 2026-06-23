# 1. Agent Loop — 从零开始，真的从零

本章参考代码：[examples/chapter-01/agent.py](../examples/chapter-01/agent.py)

## 本章目标

一步步写出一个能多轮对话的 LLM 客户端。这是 Agent Loop 的地基——没有这个，后面的工具、权限、记忆全是空中楼阁。

本章结束时你会有：一个在终端里可以反复跟 LLM 对话、且模型能记住上文的 Python 程序。

---

## Step 0：理解"客户端"是什么

你要调 LLM 的 API（比如 DeepSeek、Anthropic），就像你要打电话给一个人——你需要一部"电话"。**客户端就是这部电话**。

创建客户端本身不会发任何网络请求，它只是保存了两件事：
- **往哪里发**（base_url）
- **用什么身份**（api_key）

真正"打电话"是你调 `client.messages.create(...)` 的时候。

## Step 1：环境准备

### 安装依赖

```bash
pip install anthropic
```

`anthropic` 是 Anthropic 官方 SDK。DeepSeek 兼容 Anthropic 的 API 格式，所以用同一个 SDK 就能调 DeepSeek。

### 设置环境变量

API Key 是你的身份凭证，**绝对不要写在代码里**。把它存到环境变量：

```bash
# 在 ~/.zshrc 末尾加两行
export ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
export DEEPSEEK_API_KEY=你的deepseek_key
```

加完后重开终端，或者 `source ~/.zshrc` 让它生效。

两个环境变量的作用：
- `ANTHROPIC_BASE_URL`：告诉 SDK 把请求发到 DeepSeek 而不是 Anthropic
- `DEEPSEEK_API_KEY`：你的身份凭证（DeepSeek 的 API Key）

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

运行 `python agent.py`，如果终端打印了类似"你好！有什么可以帮助你的？"，恭喜，你成功调通了 LLM API。

### Anthropic 的消息格式

和 OpenAI 格式有几个区别：
- **system prompt 是单独的参数**，不是放在 messages 数组里
- **content 可以是字符串，也可以是列表**。简单文字直接写字符串就行：
  ```python
  {"role": "user", "content": "你好"}           # ✅ 简单写法
  {"role": "user", "content": [{"type": "text", "text": "你好"}]}  # ✅ 完整写法
  ```

### response 的结构

```
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
```
你好，我是小明
ThinkingBlock(signature='bd8a935f-cc91-429e-8a58-4f5916348fe6', thinking='用户说“你好，我是小明”。这是一个简单的自我介绍和问候。作为AI助手，我应该友好地回应，并询问有什么可以帮助的。因为用户使用了中文，所以用中文回复。', type='thinking')
TextBlock(citations=None, text='你好，小明！很高兴认识你。有什么我可以帮你的吗？😊', type='text')
> 你 是谁 你知道我是谁吗
ThinkingBlock(signature='d117dee3-6ae8-4a79-a5a0-3a5f9b3c6ad1', thinking='用户刚刚说自己叫小明，现在问我是否知道他是谁。应该基于当前对话历史回答：知道，他刚才说自己叫小明。', type='thinking')
TextBlock(citations=None, text='你刚才告诉我你叫小明，所以我知道你是小明。', type='text')
>
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

## Step 4：消息历史——模型怎么"记住"上文

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
    assistant_content = [
        {"type": "text", "text": block.text}
        for block in response.content
        if block.type == "text"
    ]
    messages.append({"role": "assistant", "content": assistant_content})
```

### 为什么要过滤 thinking block

response.content 里可能有三种 block：thinking、text、tool_use。

**Claude Code 的做法是：保存时删掉 thinking block，只保留 text 和 tool_use。**

原因：
- thinking 是模型的内部思考过程，API 不需要它来维持对话连贯性
- 保留它会白白占用上下文窗口（thinking 内容往往很长）
- 原项目参考实现也会过滤 thinking

```python
# 参考实现的做法：
final_message.content = [b for b in final_message.content if b.type != "thinking"]
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

```
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

模型每次都收到**完整的** messages 列表，所以它能"记住"之前的对话——本质上不是记忆，是你把历史全部重新喂给它了。

这也意味着对话越长，messages 越大，Token 消耗越多。后面会讲怎么压缩。

## 你现在有什么

一个能多轮对话、有记忆的 LLM 客户端。完整代码 ~20 行。

**但它只是一个聊天机器人，还不是 Agent。**

Agent 和聊天机器人的区别：**Agent 能做事**。它不只是回复文字，还能读文件、写文件、跑命令。怎么做到的？通过给模型"工具"——这是下一章的内容。

---

> **下一章**：[工具系统](./02-tools.md) —— 让模型从"只会说话"变成"能做事"。我们会定义第一个工具 `read_file`，让模型真正读取你电脑上的文件。

---

## 同步 vs 异步：这一章先不迁移

你可能在参考代码里看到 `async def`、`await` 这些关键词。简单解释：

- **同步**（你现在写的）：调 API 的 3 秒里，程序卡住等结果
- **异步**：调 API 的 3 秒里，程序可以去干别的事

你现在只有一个用户在终端交互，还没有流式输出、并行工具和异步记忆预取，因此同步版本足以完成本章目标。

这里说的是：**在当前阶段，两种版本表现出来的功能相同**。并不代表整个项目永远不需要异步。

异步能力会按下面的顺序补回：

| 章节 | 异步相关成果 |
|---|---|
| Ch2 Tools | 继续用同步代码，先理解工具调用协议和内层循环 |
| Ch4 CLI / Session | 迁移到 `AsyncAnthropic`、`async def`、`await`、`asyncio.run()` |
| Ch5 Streaming | 用异步实现流式输出、工具提前执行和并行调用 |
| Ch8 Memory | 用异步预取减少记忆召回的等待时间 |

所以第一章省略异步只是调整学习顺序，不是删掉最终能力。

## Claude Code 的做法（进阶阅读）

> 以下内容对照 Claude Code 真实源码，第一遍可以跳过。

### 双层架构

Claude Code 把 Agent Loop 拆成两层：

- **QueryEngine**（会话级）：管整个对话生命周期——用户输入处理、USD 预算检查、Token 统计、会话恢复
- **queryLoop**（单轮级）：管一次查询的执行——消息压缩、API 调用、工具执行、错误恢复

我们把两层合并成一个简单的 while 循环，够用了。

### 七种循环继续原因

Claude Code 的循环有 7 个继续位置：工具调用、Token 截断恢复、错误重试、Hook 拦截等。我们只处理第 1 种：有工具调用就继续，否则退出。

### queryLoop 用异步生成器

签名是 `async function*`，选这个而不是回调/事件的原因：
1. **背压控制**：消费端不处理完，生产端不继续
2. **线性控制流**：所有分支用普通 `continue` / `break`，不需要状态机

这些都是工程优化，不影响核心逻辑理解。
