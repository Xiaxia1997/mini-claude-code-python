# 7. 上下文管理 — 给 Agent 一个"有限但够用的记忆"

## 本章目标

到目前为止，你的 Agent 每次对话都把**所有历史消息**原封不动地发给模型。对话短的时候没问题，但模型的上下文窗口是有限的——当前 Claude API 文档里，不同模型/开关会落在 200K 或最高 1M tokens 这一档。看起来很大，但一次 `read_file`、`grep`、长日志输出很快就能把窗口顶上去；超过限制时 API 会直接报错，Agent 就废了。

本章实现 4 层分级压缩，从最轻量（截断）到最重（LLM 摘要），逐级递进：

| 维度 | 之前（Ch6 结束时） | 之后（Ch7 结束时） |
|---|---|---|
| 工具结果限制 | ✅ Ch2 已实现（`_truncate_result`，超过 50K 截断） | 不变 |
| 上下文感知 | ✅ Ch5 已有 token 计数（`total_input_tokens`） | 新增：利用率计算 + 压缩触发判断 |
| 旧内容处理 | 永远保留 | 根据利用率动态缩减、去重、清理 |
| 窗口快满时 | API 报错 | 自动调用 LLM 摘要压缩 |
| 手动压缩 | 不支持 | `/compact` 命令随时触发 |

### 4 层概览

```text
Layer 0（截断）  → 单条工具结果超过 50K 字符就砍掉中间（Ch2 已实现）
Layer 1（Budget）→ 窗口快满时，回头把旧的工具结果缩到更短的预算
Layer 2（Snip）  → 同一个文件读了两次，只保留最新那次的结果
Layer 3（Compact）→ 窗口真的满了，用 LLM 把整段对话压缩成摘要
```

每层比上一层更激进，但也更贵（Layer 3 要额外花一次 API 调用）。总是先尝试便宜的手段，不够了才升级。

### 文件分工更新

| 文件 | 本章改动 |
|---|---|
| `tools.py` | 不变（`_truncate_result()` 已在 Ch2 实现） |
| `agent.py` | 新增利用率计算 + Layer 1/2/3 压缩方法 + `/compact` 命令 |

参考代码：

- [`agent.py`](../examples/chapter-07/agent.py)
- [`tools.py`](../examples/chapter-07/tools.py)
- [`prompt.py`](../examples/chapter-07/prompt.py)
- [`session.py`](../examples/chapter-07/session.py)
- [`ui.py`](../examples/chapter-07/ui.py)

---

## Layer 0 回顾：截断工具结果（已实现）

> Ch2 已经在 `tools.py` 里实现了 `_truncate_result()`，超过 50K 字符的工具结果会保留头尾、截掉中间。这就是 4 层压缩里最简单的 Layer 0。

Layer 0 的作用：防止单次工具调用（比如读了一个巨大的日志文件）直接把上下文窗口撑爆。但它只管"这一次工具结果太大"的问题，不管整个对话越来越长的问题——那是 Layer 1/2/3 要解决的。

---

## Layer 1：Budget — 根据利用率动态缩减旧工具结果

> 文件：`agent.py`

### 为什么需要 Layer 1

Layer 0 是在工具执行时做的一次性截断，最多把单条结果压到 50K。但随着对话进行，早期的工具结果可能还是很长——它们在当时没超限，或者已经被压到 50K，但现在窗口快满了，就需要回去把它们继续压到 30K / 15K。

Layer 1 解决的是"整个对话越来越长"的问题：每次 API 调用前，回头扫描历史里的旧 `tool_result`，根据当前窗口有多挤，决定每条旧工具结果最多还能保留多少字符。

### 为什么优先压缩 tool_result

上下文里最容易变大的不是用户问题，而是工具结果。一次 `read_file`、`grep_search` 或 `run_shell` 可能返回几万字符，而用户输入通常只有几十到几百字。

`tool_result` 也比较适合先压缩：

1. 它通常最大，压缩收益最高
2. 它经常重复，比如同一个文件被读了多次
3. 它可以重新获取，模型需要时可以再次调用工具
4. 它是数据，不是用户意图；用户需求和模型决策更应该保留

所以 Layer 1 和 Layer 2 都先处理 `tool_result`。等这些便宜手段不够用了，才进入 Layer 3，用 LLM 把整段对话摘要化。

### 两个概念：利用率和 Budget

| 概念 | 单位 | 回答的问题 | 本章怎么得到 |
|---|---|---|---|
| 利用率 | 百分比 | 请求占了模型上下文窗口多少？ | `last_input_token_count / effective_window` |
| Budget | 字符数 | 每条旧 `tool_result` 最多保留多少字符？ | <50% 保持 50K；50%-70% 压到 30K；>70% 压到 15K |

Ch5 已经有 `total_input_tokens` 和 `total_output_tokens`，但这两个数是多轮请求的累计值，适合算费用，不适合判断"现在上下文快满了吗"。压缩管道要看的是 `last_input_token_count`：最近一次 API 请求实际发进去的输入 token 数。

执行顺序是：

1. API 返回后，记录这次请求的 `input_tokens`
2. 下一次 API 调用前，用它算利用率，并得到当前 Budget
3. 如果当前 Budget 小于旧 `tool_result` 的长度，就截断

### 在 `__init__` 中新增

```python
class Agent:
    def __init__(self, ...):
        # ... 已有的初始化代码 ...

        # ── 上下文管理（Ch7 新增） ──
        self.model_window = 1_000_000        # 模型上下文窗口大小（示例按 1M 配置）
        self.effective_window = self.model_window - 50_000  # 预留给新 I/O
        self.last_input_token_count = 0      # 上一次 API 调用的输入 token 数
```

**`effective_window`** 为什么要减 50K？模型窗口不能用满——还要给新一轮的用户输入、模型回复和工具调用留空间。这里按 1M 窗口预留 50K，是为了让自动压缩更早发生；如果你用的是 200K 窗口模型，可以把 `model_window` 改成 200_000，把预留改成 20_000。

### 每次 API 调用后更新

在 `chat()` 方法中，API 响应返回后，**在已有的 token 累加代码旁边**，加一行记录本轮输入 token 数：

```python
self.last_input_token_count = response.usage.input_tokens  # Ch7 新增
self.total_input_tokens += response.usage.input_tokens      # Ch5 已有
self.total_output_tokens += response.usage.output_tokens    # Ch5 已有
```

### 利用率计算

```python
utilization = self.last_input_token_count / self.effective_window
```

- `utilization < 0.5`：窗口还很宽裕，不用管
- `0.5 <= utilization < 0.7`：开始有点挤了
- `0.7 <= utilization < 0.85`：需要更积极地压缩
- `utilization >= 0.85`：快满了，得做全量摘要

### 根据利用率决定 Budget

Budget 的意思是"每条旧 `tool_result` 最多保留多少字符"：

| 利用率 | 当前 Budget | 谁负责 |
|---|---|---|
| < 50% | 50K 字符 | Layer 0 已经保证，Layer 1 不动 |
| 50%-70% | 30K 字符 | Layer 1 二次压缩 |
| > 70% | 15K 字符 | Layer 1 更积极压缩 |

Layer 1 的完整执行链是：

```text
chat()
  -> _budget_tool_results()
  -> API 调用
```

`_budget_tool_results()` 负责两件事：

1. 根据利用率决定当前 Budget
2. 扫描历史消息，把超过 Budget 的旧 `tool_result` 截短

这里先直接在 `chat()` 里调用 `_budget_tool_results()`。等 Layer 2 也写完后，再新增 `_run_compression_pipeline()` 把多个压缩步骤统一包起来。

下面先看一个会用到的 Python 类型检查语法，再写完整函数。

### 新概念：isinstance()

Python 中的数据有不同类型（字符串、列表、字典等）。`isinstance()` 用来检查一个值是不是某个类型：

```python
x = [1, 2, 3]
isinstance(x, list)    # True
isinstance(x, str)     # False
isinstance(x, dict)    # False
```

消息中的 `content` 字段有时是字符串、有时是列表，我们需要区分：

```python
text_msg = {"role": "user", "content": [{"type": "text", "text": "你好"}]}
tool_msg = {"role": "user", "content": [{"type": "tool_result", "content": "..."}]}

isinstance(text_msg["content"], list)  # True
isinstance(tool_msg["content"], list)  # True
```

注意：`content` 是列表，不代表它一定是 `tool_result`。用户的普通文本消息也可能是列表，因为 Anthropic 的消息内容是由一个个 block 组成的。

所以这里其实分两步判断：

1. 先用 `isinstance(msg.get("content"), list)` 确认它能不能被当作 block 列表遍历
2. 再用 `block.get("type") == "tool_result"` 判断这个 block 是不是工具结果

不能只看 `type` 的原因是：`type` 在每个 `block` 里面，不在 `msg["content"]` 这个列表本身上。必须先确认 `content` 是列表，才能安全地进入 `for block in msg["content"]`。

### 代码

文件：`agent.py`

先在 `Agent` 类里新增 `_budget_tool_results()`。它和 `chat()` 是同级方法，不是写进 `chat()` 里面。

```python
def _budget_tool_results(self):
    """Layer 1: 根据上下文利用率，动态缩减历史 tool_result 的大小"""
    # 1. 算利用率：上一次输入 token / 安全窗口；如果窗口为 0 就返回 0，避免除以零
    utilization = (self.last_input_token_count / self.effective_window
                   if self.effective_window else 0)

    # 2. 低于 50% 说明窗口还很宽裕，沿用 Layer 0 的 50K 上限
    if utilization < 0.5:
        return

    # 3. 双阈值预算：70% 以上用更紧的 15K，50-70% 用较松的 30K
    budget = 15000 if utilization > 0.70 else 30000

    # 4. 扫描候选消息：user + list 只说明 content 可以按 block 遍历
    for msg in self.messages:
        if msg.get("role") != "user" or not isinstance(msg.get("content"), list):
            continue
        for block in msg["content"]:
            # 5. 真正判断工具结果靠 type；还要确认 content 是字符串且长度超预算
            if (isinstance(block, dict)
                    and block.get("type") == "tool_result"
                    and isinstance(block.get("content"), str)
                    and len(block["content"]) > budget):
                # 6. 截断逻辑：和 Layer 0 一样保留头尾，但预算更小
                keep = (budget - 80) // 2
                original_len = len(block["content"])
                block["content"] = (
                    block["content"][:keep]
                    + f"\n\n[... budgeted: {original_len - keep * 2} chars truncated ...]\n\n"
                    + block["content"][-keep:]
                )
```

这里真正截断的是这一段：

```python
keep = (budget - 80) // 2
original_len = len(block["content"])
block["content"] = (
    block["content"][:keep]
    + f"\n\n[... budgeted: {original_len - keep * 2} chars truncated ...]\n\n"
    + block["content"][-keep:]
)
```

它的意思是：当前 Budget 先减掉中间提示文字大约占用的长度，再把剩下空间分成两半，一半保留开头，一半保留结尾。这样模型还能看到文件/输出的开头结构和末尾错误信息。

最后在 `chat()` 的 API 调用前直接调用它：

```python
while True:
    self._budget_tool_results()

    response = await self._call_stream()
```

为什么放在 API 调用前？因为 `_budget_tool_results()` 会原地修改 `self.messages` 里的旧 `tool_result`。先压缩，再调用 API，这次请求发给模型的上下文才会变小。

这里不要再写 `self.client.messages.create(...)`。Ch5 已经把主聊天请求改成了 streaming，并封装在 `_call_stream()` 里；`model`、`max_tokens`、`system`、`messages`、`tools` 这些参数都在 `_call_stream()` 内部传给 `self.client.messages.stream(...)`。

---

## Layer 2：Snip — 去除重复的工具结果

> 文件：`agent.py`

### 为什么 Layer 1 还不够

Layer 1 只解决"单条工具结果太长"的问题：50K 可以压到 30K / 15K。但如果同一个文件被读了好几次，旧版本内容即使已经只有 15K，也可能没有继续保留的价值。

一个常见场景：

```text
read_file("src/app.py")   # 先看原文件
edit_file("src/app.py")   # 改了一行
read_file("src/app.py")   # 再读一次确认修改
```

这不是模型在乱用工具，第三步是合理的验证。但验证完成后，上下文里就同时有两份 `src/app.py` 内容。旧的那份已经过时了，继续占窗口很浪费。

Snip 的策略：**同一文件读取多次，只保留最新一次，旧的替换成占位符。**

### 关键设计：只清内容，保留骨架

压缩前，消息历史里可能有两次读取同一个文件：

```python
self.messages = [
    {
        "role": "assistant",
        "content": [{
            "type": "tool_use",
            "id": "toolu_1",
            "name": "read_file",
            "input": {"file_path": "python/mini_claude/agent.py"},
        }],
    },
    {
        "role": "user",
        "content": [{
            "type": "tool_result",
            "tool_use_id": "toolu_1",
            "content": "agent.py 的旧完整内容......",
        }],
    },
    {
        "role": "assistant",
        "content": [{
            "type": "tool_use",
            "id": "toolu_2",
            "name": "read_file",
            "input": {"file_path": "python/mini_claude/agent.py"},
        }],
    },
    {
        "role": "user",
        "content": [{
            "type": "tool_result",
            "tool_use_id": "toolu_2",
            "content": "agent.py 的最新完整内容......",
        }],
    },
]
```

Snip 后，不删除旧消息，只把旧 `tool_result` 的大块内容换成占位符：

```python
{
    "role": "user",
    "content": [{
        "type": "tool_result",
        "tool_use_id": "toolu_1",
        "content": "[Content snipped - newer version available below]",
    }],
}
```

**Snip 按文件路径去重：同一个文件被读了多次，只保留最后一次的完整结果，之前的全部换成占位符。** 不管文件内容有没有变过。

Snip 会丢信息吗？**会。** 被 snip 掉的旧内容就是没了——上下文里没了，磁盘上的文件也可能已经被改过了，旧版本回不来（除非靠 Git）。

Claude Code 的完整思路其实是两步配合：

1. **先持久化**：工具结果超过 30K 时，`persistLargeResult` 把完整内容写到磁盘（`~/.mini-claude/tool-results/xxx.txt`），上下文里只放预览 + 磁盘路径
2. **再 Snip**：把旧的上下文内容换成占位符 `[Content snipped - re-read if needed]`

因为第 1 步已经把完整内容存到了磁盘，所以 Snip 之后模型真的可以 `read_file` 那个磁盘路径，把内容读回来。**先存后删，所以不丢。**

我们的入门版**省略了第 1 步（磁盘持久化）**，直接做 Snip。这意味着 Snip 在我们这里是有损操作——旧内容被清掉后就真的没了。占位符也因此改成了 `[Content snipped - newer version available below]`，不再暗示可以 re-read。

这个取舍是可接受的：Snip 清掉的要么是过时的旧版本（文件改过了），要么是重复的副本（文件没改，最新那次的内容还在）。模型继续工作需要的是最新版本，不需要旧版本。

为什么不能直接删掉旧的 `tool_result` 消息？因为 Anthropic 要求 `assistant` 的 `tool_use` 后面必须有对应的 `user` `tool_result`。如果整条消息删了，`toolu_1` 就失去配对，API 会报错。所以只能把 `content` 换成占位符，保留消息结构。

### 新概念：集合（set）

Python 的 `set` 是一个"不重复的集合"——用来快速检查"这个东西见没见过"：

```python
seen = set()
seen.add("src/app.py")

"src/app.py" in seen     # True —— 见过
"src/main.py" in seen    # False —— 没见过
```

`set` 的查找速度是 O(1)（常数时间），不管里面有多少元素。用列表查找是 O(n)，元素越多越慢。

### 代码

这一小节只改 `agent.py`，分成两处：

1. **模块级常量**：写在文件顶部，`import` 语句后面、`class Agent:` 前面。不要写进 `__init__()`、`chat()` 或任何方法里面
2. **两个类方法**：`_snip_stale_results()` 和 `_find_tool_info()` 写在 `class Agent` 里面，和 `chat()`、`_budget_tool_results()` 同级

在文件顶部、`class Agent:` 前面，添加三个常量：

```python
from tools import tool_definitions, execute_tool

SNIPPABLE_TOOLS = {"read_file", "grep_search", "list_files", "run_shell"}  # 哪些工具的结果可以被 snip
SNIP_PLACEHOLDER = "[Content snipped - newer version available below]"      # 替换旧内容用的占位符
KEEP_RECENT_RESULTS = 3                                                     # 最近 3 个 tool_result 永远不 snip

class Agent:
    def __init__(self, ...):
        ...
```

- **`SNIPPABLE_TOOLS`**：白名单。只有这四个工具的结果会被 snip。`edit_file`、`write_file` 的确认信息不在里面——snip 掉的话模型就不知道自己改过什么了
- **`SNIP_PLACEHOLDER`**：旧内容被清掉后替换成的占位符文本。模型看到它就知道"这里之前有内容，但更新的版本在下面"
- **`KEEP_RECENT_RESULTS`**：保护最近 3 个 `tool_result` 不被 snip，即使它们符合去重条件。避免刚拿到的结果马上被清掉

然后在 `class Agent` 里面添加 `_snip_stale_results()`，和 `chat()`、`_budget_tool_results()` 同级。

```python
def _snip_stale_results(self):
    """Layer 2: 替换重复的工具结果为占位符"""
    utilization = (self.last_input_token_count / self.effective_window
                   if self.effective_window else 0)
    if utilization < 0.6:
        return  # 利用率不高，先不动

    # 第一遍：从后往前扫描，记录每个文件最后一次被读取的位置
    # key = (工具名, 文件路径), value = 该 tool_result 在消息列表中的位置
    latest_occurrence = {}
    tool_result_positions = []  # 记录所有 tool_result 的位置

    for msg_idx, msg in enumerate(self.messages):
        if msg.get("role") != "user" or not isinstance(msg.get("content"), list):
            continue
        for block_idx, block in enumerate(msg["content"]):
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue

            tool_result_positions.append((msg_idx, block_idx))

            # 找到对应的 tool_use（在前一条 assistant 消息中）
            tool_use_id = block.get("tool_use_id")
            tool_name, file_key = self._find_tool_info(msg_idx, tool_use_id)

            if tool_name in SNIPPABLE_TOOLS and file_key:
                key = (tool_name, file_key)
                latest_occurrence[key] = (msg_idx, block_idx)

    # 最近 N 个 tool_result 永远不 snip
    protected = set(tool_result_positions[-KEEP_RECENT_RESULTS:])

    # 第二遍：把不是最新、也不受保护的 tool_result 替换为占位符
    for msg_idx, msg in enumerate(self.messages):
        if msg.get("role") != "user" or not isinstance(msg.get("content"), list):
            continue
        for block_idx, block in enumerate(msg["content"]):
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            if (msg_idx, block_idx) in protected:
                continue

            tool_use_id = block.get("tool_use_id")
            tool_name, file_key = self._find_tool_info(msg_idx, tool_use_id)

            if tool_name in SNIPPABLE_TOOLS and file_key:
                key = (tool_name, file_key)
                if latest_occurrence.get(key) != (msg_idx, block_idx):
                    block["content"] = SNIP_PLACEHOLDER
```

### 辅助方法：_find_tool_info

Snip 需要知道每个 `tool_result` 对应的是什么工具、操作的是什么文件。这些信息在前一条 `assistant` 消息的 `tool_use` 块中。

这个方法也写在 `class Agent` 里面，和 `_snip_stale_results()` 同级：

```python
def _find_tool_info(self, tool_result_msg_idx, tool_use_id):
    """根据 tool_result 的位置和 ID，找到对应的工具名和文件路径"""
    # tool_result 消息的前一条应该是 assistant 消息（包含 tool_use）
    if tool_result_msg_idx == 0:
        return None, None

    prev_msg = self.messages[tool_result_msg_idx - 1]
    if prev_msg.get("role") != "assistant" or not isinstance(prev_msg.get("content"), list):
        return None, None

    for block in prev_msg["content"]:
        if (isinstance(block, dict)
                and block.get("type") == "tool_use"
                and block.get("id") == tool_use_id):
            tool_name = block.get("name")
            tool_input = block.get("input", {})
            # 提取文件路径作为去重 key
            file_key = (tool_input.get("file_path")
                        or tool_input.get("path")
                        or tool_input.get("command", ""))
            return tool_name, file_key

    return None, None
```

这里做了一个简化：对于 `grep_search` 和 `run_shell`，用 `command` 参数作为去重 key。同样的搜索命令跑两次，旧结果可以 snip。

### 为什么从后往前找"最新"

```python
latest_occurrence[key] = (msg_idx, block_idx)
```

遍历是从前往后的（`enumerate` 的默认顺序）。同一个 key 出现多次时，后面的会覆盖前面的，最终 `latest_occurrence` 里存的就是最后一次（最新的）。这比从后往前遍历更简单——不需要 `reversed()`。

---

## Layer 3：Auto-compact — LLM 摘要压缩

> 文件：`agent.py`

前三层都是"修剪"——在不调用模型的情况下减少 token。但如果对话真的很长，修剪还不够，就需要最后手段：**让模型把整段对话总结成一段话**。

这是最贵的一层（需要额外调一次 API），但也是最有效的——可以把几万 token 压缩成几百。

### 压缩后的消息结构

```text
压缩前（假设 20 条消息）：
  user: "帮我改 bug"
  assistant: tool_use(read_file, ...)
  user: tool_result(文件内容...)
  assistant: "我看到问题了..."
  ... (16 条更多消息) ...
  user: "现在帮我加个功能"    ← 最新的用户消息

压缩后（3 条消息）：
  user: "[Previous conversation summary]\n之前讨论了..."
  assistant: "Understood. I have the context from our previous conversation. How can I continue helping?"
  user: "现在帮我加个功能"    ← 最新的用户消息保留
```

压缩把所有历史替换成"摘要 + 确认 + 最新输入"三条消息。模型看到摘要就知道之前做了什么，然后继续处理最新的请求。

这也解释了为什么 Auto-compact 是最后手段：它会把原来的长消息前缀整体改写成摘要。无论是 Anthropic 原生 prompt cache，还是 DeepSeek 默认启用的 Context Caching，只要前缀被改写，旧历史对应的缓存命中都会下降。本教程暂时不做缓存感知，只把 Auto-compact 当作上下文快满时的最后手段。

### 为什么要有 "Understood" 那条假消息

Anthropic API 要求消息必须 user/assistant 交替出现。压缩后第一条是 user（摘要），第三条也是 user（最新输入），中间必须插一条 assistant，否则 API 会报错。

### 代码

```python
async def _compact_conversation(self):
    """Layer 3: 调用 LLM 把整段对话压缩成一段摘要"""
    if len(self.messages) < 4:
        return  # 消息太少，没必要压缩

    last_user_msg = self.messages[-1]

    summary_instruction = {
        "role": "user",
        "content": "Summarize the conversation so far in a concise paragraph, "
                   "preserving key decisions, file paths, and context needed "
                   "to continue the work.",
    }

    # 复用同一个 system prompt，把摘要指令追加到消息末尾
    summary_response = await self.client.messages.create(
        model=self.model,
        max_tokens=2048,
        system=self.system_prompt,
        messages=[*self.messages[:-1], summary_instruction],
    )

    # 提取摘要文本
    summary_text = "No summary available."
    if summary_response.content and summary_response.content[0].type == "text":
        summary_text = summary_response.content[0].text

    # 用 3 条消息替换整个历史
    self.messages = [
        {
            "role": "user",
            "content": f"[Previous conversation summary]\n{summary_text}",
        },
        {
            "role": "assistant",
            "content": "Understood. I have the context from our previous conversation. "
                       "How can I continue helping?",
        },
    ]

    # 把最新的用户消息加回来
    if last_user_msg.get("role") == "user":
        self.messages.append(last_user_msg)

    # 重置 token 计数（压缩后上下文很小了）
    self.last_input_token_count = 0
```

### 新概念：`*self.messages[:-1]`（列表解包 + 切片）

这个表达式做了两件事：

```python
self.messages[:-1]   # 切片：取除最后一个之外的所有元素
*self.messages[:-1]  # 解包：把列表里的元素一个个拿出来
```

放在列表里就是拼接：

```python
messages = [
    *self.messages[:-1],     # 旧消息展开
    {"role": "user", ...},  # 加一条新的
]
# 等价于 self.messages[:-1] + [{"role": "user", ...}]
```

`*` 解包比 `+` 拼接好在哪里？当你要在列表中间插入元素时更灵活——不需要嵌套多层 `+`。

### 为什么 `len(self.messages) < 4` 就不压缩

4 条消息 = 2 轮对话（user + assistant + user + assistant）。比这更少的话，摘要后的 3 条消息可能比原始消息还长，压缩没有意义。

### 关键：summary_response 的 ThinkingBlock 处理

如果你用的是 DeepSeek（开了思考模式），`summary_response.content[0]` 可能是 ThinkingBlock 而不是 TextBlock。所以检查 `.type == "text"` 非常重要——跳过思考块，只取文本。

---

## 编排压缩管道

> 文件：`agent.py`

4 层已经各自实现了。现在把它们串起来，在正确的时机执行。

### 压缩管道：Layer 1-2 在每次 API 调用前

```python
def _run_compression_pipeline(self):
    """在每次 API 调用前执行 Layer 1-2 压缩（零 API 成本）"""
    self._budget_tool_results()    # Layer 1: 动态缩减
    self._snip_stale_results()     # Layer 2: 去重
```

顺序有讲究：
1. **先 Budget 再 Snip**：Budget 先把大结果压小，Snip 再来判断哪些是重复的。如果反过来，Snip 可能会保留一个 30K 的"最新"结果不动，Budget 再去处理时效率降低
2. **两层都是零成本的**：只是字符串操作，不调 API，每次执行都很快

### 自动压缩检查：Layer 3 在 turn boundary

```python
async def _check_and_compact(self):
    """在 turn boundary 检查是否需要 Layer 3 全量摘要"""
    if self.last_input_token_count > self.effective_window * 0.85:
        print_info("Context window filling up, compacting conversation...")
        await self._compact_conversation()
```

### 什么是 turn boundary

```text
用户输入 "帮我改个 bug"
    ↓
context.append(用户消息)     ← turn boundary 在这里
    ↓
_check_and_compact()         ← 在这里检查是否需要 Layer 3
    ↓
while True:                  ← 进入工具循环
    _run_compression_pipeline()  ← 每次 API 调用前做 Layer 1-2
    API 调用
    处理工具...
    如果没有工具调用 → break
```

**Layer 3 必须在 turn boundary 调用，绝对不能在工具循环中间调用。**原因：

`_compact_conversation()` 会把 `self.messages[-1]` 当作"最新的用户文本消息"保留。在 turn boundary，最后一条确实是 `{"role": "user", "content": "帮我改个 bug"}`。

但在工具循环中间，最后一条是 `{"role": "user", "content": [{"type": "tool_result", ...}]}`。如果这时执行压缩：

1. 摘要会把 `context[:-1]` 全部压缩——但 `context[-2]` 是 assistant 的 `tool_use`
2. 压缩后，这条 `tool_use` 消失了，但 `tool_result` 还在
3. API 看到 `tool_result` 但找不到配对的 `tool_use`，直接报错

这就是为什么叫"turn boundary 契约"——只在用户输入推入消息之后、工具循环开始之前调用。

### 接入 chat() 方法

在你的 `chat()` 方法中：

```python
async def chat(self, user_input):
    # 把用户消息加入上下文
    self.messages.append({
        "role": "user",
        "content": [{"type": "text", "text": user_input}],
    })

    # ── turn boundary: 检查是否需要 auto-compact ──
    await self._check_and_compact()

    while True:
        # ── 每次 API 调用前：执行 Layer 1-2 压缩 ──
        self._run_compression_pipeline()

        response = await self._call_stream()

        # 更新 token 统计（Layer 1 中添加的）
        self.last_input_token_count = response.usage.input_tokens
        self.total_input_tokens += response.usage.input_tokens
        self.total_output_tokens += response.usage.output_tokens
        self.last_api_call_time = time.time()

        # ... 处理 response.content（text / tool_use）...
        # ... 如果没有工具调用就 break ...
```

---

## 添加 `/compact` 命令

> 文件：`agent.py`

有时用户想在利用率还没到 85% 时就手动压缩（比如对话跑偏了，想"清理一下"）。

### 在 Agent 类中添加公开方法

```python
async def compact(self):
    """手动触发对话压缩（供 /compact 命令调用）"""
    await self._compact_conversation()
    print_info("Conversation compacted.")
```

### 在 REPL 中接入

在你的 `run_repl()` 函数中（Ch4 已有的 REPL 循环），添加对 `/compact` 的处理：

```python
async def run_repl(agent):
    # ... 已有代码 ...
    while True:
        # ... 读取输入 ...
        inp = line.strip()

        if inp == "/clear":
            agent.clear_history()
            continue
        if inp == "/cost":
            agent.show_cost()
            continue
        if inp == "/compact":              # 新增
            await agent.compact()          # 新增
            continue                       # 新增

        # ... 调用 agent.chat(inp) ...
```

### 更新 welcome 信息

在 `ui.py` 的 `print_welcome()` 中，把 `/compact` 加入命令提示：

```python
def print_welcome():
    console.print("[dim]  Commands: /clear /cost /compact[/dim]\n")
```

---

## 测试压缩管道

### 测试 1：Layer 0 截断

```text
> 运行一下 cat /dev/urandom | head -c 200000 | base64
```

这条命令会输出约 270K 字符的随机文本。`_truncate_result()` 应该把它截断到 50K，中间显示 `[... truncated N chars ...]`。

检查模型的回复——它应该提到文件被截断了，而不是直接报错。

### 测试 2：Layer 1-2 触发

手动对话 10-20 轮，多次读取同一个文件：

```text
> 读一下 agent.py
> 把第 10 行改成 xxx
> 再读一下 agent.py 确认修改
> 读一下 tools.py
> 再读一下 agent.py
```

在 `_snip_stale_results` 入口加一行 `print(f"[DEBUG] utilization={utilization:.2f}")` 观察利用率变化。当利用率超过 0.6，你应该能看到旧的 `read_file` 结果被替换成 `[Content snipped - newer version available below]`。

### 测试 3：Layer 3 自动压缩

要触发 85% 利用率需要非常长的对话。最简单的测试方式是临时把阈值调低：

```python
# 临时修改，测试完改回来
if self.last_input_token_count > self.effective_window * 0.20:  # 改成 20% 方便测试
```

然后对话几轮，应该看到 `Context window filling up, compacting conversation...` 提示。压缩后，前面的对话内容消失了，只剩一段摘要。模型仍然知道之前做过什么（从摘要中读取），可以继续工作。

### 测试 4：/compact 手动压缩

```text
> 帮我读一下 tools.py 然后解释一下
... (模型回复) ...
> /compact
  i Conversation compacted.
> 刚才我们在讨论什么？
... (模型应该能从摘要中回忆起刚才的对话) ...
```

---

## 回头看：4 层压缩的分工

```text
工具执行                API 调用前               Turn boundary
    |                       |                        |
    v                       v                        v
Layer 0: 截断          Layer 1: Budget            Layer 3: Auto-compact
（50K 硬限制）        （30K/15K 动态预算）       （LLM 摘要，最后手段）
                       Layer 2: Snip
                      （去重，占位符替换）
```

每一层的触发条件和成本：

| Layer | 触发条件 | 直接成本 | 缓存代价 | 压缩力度 |
|---|---|---|---|---|
| 0 截断 | 工具结果 > 50K 字符 | 零 | 无（执行时截断，还没进入消息历史） | 单个结果 |
| 1 Budget | 利用率 > 50% | 零 | 有：被改的消息及之后的缓存失效 | 所有旧 tool_result |
| 2 Snip | 利用率 > 60% | 零 | 有：被改的消息及之后的缓存失效 | 重复的 tool_result |
| 3 Compact | 利用率 > 85% | 1 次 API 调用 | 大：整个消息历史被替换，缓存全部失效 | 整个对话历史 |

越往后越激进、越贵、越晚触发。这就是"渐进式压缩"——用最便宜的手段能撑多久就撑多久。

Layer 1/2 虽然不调 API，但修改 `self.messages` 会破坏 KV cache 的前缀匹配（详见下方"压缩与缓存的矛盾"）。所以它们也不是完全免费的——省了上下文空间，但部分 token 从缓存价（0.1x）变回了原价（1x）。这也是为什么要设利用率门槛：窗口不挤的时候不压缩，避免白白付出缓存代价。

---

## 完整参考代码

### tools.py（在 Ch6 基础上增加截断）

在 `execute_tool()` 上方添加：

```python
MAX_RESULT_CHARS = 50000

def _truncate_result(result):
    """如果工具结果超过 50K 字符，保留头尾，截掉中间"""
    if len(result) <= MAX_RESULT_CHARS:
        return result
    keep_each = (MAX_RESULT_CHARS - 60) // 2
    return (
        result[:keep_each]
        + f"\n\n[... truncated {len(result) - keep_each * 2} chars ...]\n\n"
        + result[-keep_each:]
    )
```

修改 `execute_tool()` 的 return：

```python
return _truncate_result(handler(**args))
```

### agent.py（新增上下文管理）

以下是本章在 Agent 类中新增的所有方法和属性，汇总在一起：

```python
import time

# ── 常量 ──
SNIPPABLE_TOOLS = {"read_file", "grep_search", "list_files", "run_shell"}
SNIP_PLACEHOLDER = "[Content snipped - newer version available below]"
KEEP_RECENT_RESULTS = 3


class Agent:
    def __init__(self, api_key, base_url=None):
        # ... 已有的初始化代码 ...

        # ── 上下文管理 ──
        self.model_window = 1_000_000
        self.effective_window = self.model_window - 50_000
        self.last_input_token_count = 0
        self.last_api_call_time = 0

    # ── Layer 1: Budget ──

    def _budget_tool_results(self):
        utilization = (self.last_input_token_count / self.effective_window
                       if self.effective_window else 0)
        if utilization < 0.5:
            return
        budget = 15000 if utilization > 0.70 else 30000

        for msg in self.messages:
            if msg.get("role") != "user" or not isinstance(msg.get("content"), list):
                continue
            for block in msg["content"]:
                if (isinstance(block, dict)
                        and block.get("type") == "tool_result"
                        and isinstance(block.get("content"), str)
                        and len(block["content"]) > budget):
                    keep = (budget - 80) // 2
                    original_len = len(block["content"])
                    block["content"] = (
                        block["content"][:keep]
                        + f"\n\n[... budgeted: {original_len - keep * 2} chars truncated ...]\n\n"
                        + block["content"][-keep:]
                    )

    # ── Layer 2: Snip ──

    def _find_tool_info(self, tool_result_msg_idx, tool_use_id):
        if tool_result_msg_idx == 0:
            return None, None
        prev_msg = self.messages[tool_result_msg_idx - 1]
        if prev_msg.get("role") != "assistant" or not isinstance(prev_msg.get("content"), list):
            return None, None
        for block in prev_msg["content"]:
            if (isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("id") == tool_use_id):
                tool_name = block.get("name")
                tool_input = block.get("input", {})
                file_key = (tool_input.get("file_path")
                            or tool_input.get("path")
                            or tool_input.get("command", ""))
                return tool_name, file_key
        return None, None

    def _snip_stale_results(self):
        utilization = (self.last_input_token_count / self.effective_window
                       if self.effective_window else 0)
        if utilization < 0.6:
            return

        latest_occurrence = {}
        tool_result_positions = []

        for msg_idx, msg in enumerate(self.messages):
            if msg.get("role") != "user" or not isinstance(msg.get("content"), list):
                continue
            for block_idx, block in enumerate(msg["content"]):
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                tool_result_positions.append((msg_idx, block_idx))
                tool_use_id = block.get("tool_use_id")
                tool_name, file_key = self._find_tool_info(msg_idx, tool_use_id)
                if tool_name in SNIPPABLE_TOOLS and file_key:
                    latest_occurrence[(tool_name, file_key)] = (msg_idx, block_idx)

        protected = set(tool_result_positions[-KEEP_RECENT_RESULTS:])

        for msg_idx, msg in enumerate(self.messages):
            if msg.get("role") != "user" or not isinstance(msg.get("content"), list):
                continue
            for block_idx, block in enumerate(msg["content"]):
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                if (msg_idx, block_idx) in protected:
                    continue
                tool_use_id = block.get("tool_use_id")
                tool_name, file_key = self._find_tool_info(msg_idx, tool_use_id)
                if tool_name in SNIPPABLE_TOOLS and file_key:
                    if latest_occurrence.get((tool_name, file_key)) != (msg_idx, block_idx):
                        block["content"] = SNIP_PLACEHOLDER

    # ── Layer 3: Auto-compact ──

    async def _compact_conversation(self):
        if len(self.messages) < 4:
            return
        last_user_msg = self.messages[-1]

        summary_instruction = {
            "role": "user",
            "content": "Summarize the conversation so far in a concise "
                       "paragraph, preserving key decisions, file paths, "
                       "and context needed to continue the work.",
        }

        summary_response = await self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=self.system_prompt,
            messages=[*self.messages[:-1], summary_instruction],
        )

        summary_text = "No summary available."
        if (summary_response.content
                and summary_response.content[0].type == "text"):
            summary_text = summary_response.content[0].text

        self.messages = [
            {
                "role": "user",
                "content": f"[Previous conversation summary]\n{summary_text}",
            },
            {
                "role": "assistant",
                "content": "Understood. I have the context from our previous "
                           "conversation. How can I continue helping?",
            },
        ]

        if last_user_msg.get("role") == "user":
            self.messages.append(last_user_msg)
        self.last_input_token_count = 0

    # ── 管道编排 ──

    def _run_compression_pipeline(self):
        self._budget_tool_results()
        self._snip_stale_results()

    async def _check_and_compact(self):
        if self.last_input_token_count > self.effective_window * 0.85:
            print_info("Context window filling up, compacting conversation...")
            await self._compact_conversation()

    # ── 公开方法 ──

    async def compact(self):
        await self._compact_conversation()
        print_info("Conversation compacted.")
```

---

## 本章完成检查

- [ ] `tools.py`：`_truncate_result()` 能截断超过 50K 的工具结果
- [ ] `agent.py`：`__init__` 中有 `model_window`、`effective_window`、`last_input_token_count`、`last_api_call_time`
- [ ] `agent.py`：每次 API 调用后更新 token 统计和时间戳
- [ ] `agent.py`：`_budget_tool_results()` 根据利用率动态截断旧 tool_result
- [ ] `agent.py`：`_snip_stale_results()` 去重同文件读取，保留最近 3 个
- [ ] `agent.py`：`_compact_conversation()` 能调 LLM 生成摘要并替换历史
- [ ] `agent.py`：`_run_compression_pipeline()` 在每次 API 调用前执行
- [ ] `agent.py`：`_check_and_compact()` 只在 turn boundary 执行
- [ ] REPL 支持 `/compact` 命令
- [ ] 长对话后能看到压缩触发的提示

---

## 暂时省略的能力

| 省略内容 | 原因 | 补回章节 | 对齐参考实现的哪个能力 |
|---|---|---|---|
| Microcompact（缓存冷启动清理） | 需要理解 prompt cache 的 TTL 机制，且本地测试难以观察效果 | 可作为本章进阶练习 | `agent.py` 的 `_microcompact_anthropic()` |
| 大结果持久化到磁盘 | 需要文件系统管理 + 路径约定，当前截断已够用 | 按需补入 | 参考实现的 `persistLargeResult()` |
| Token 估算（锚点 + 字符数 / 4） | 直接用 API 返回的 `usage` 已经够准 | 当需要在 API 调用前精确预判时 | Claude Code 的 anchor + estimate |
| Prompt cache 感知 | DeepSeek 有自动 Context Caching，但 Anthropic 兼容接口会忽略 `cache_control`，本章先不做缓存断点控制 | 使用 Anthropic 原生 API 或需要优化 DeepSeek 缓存命中时 | Claude Code 的缓存断点管理 |
| 压缩后恢复（最近文件 + 活跃技能） | 需要 Memory 和 Skills 系统配合 | Ch8 Memory + Ch9 Skills 之后 | Claude Code 的 post-compact recovery |
| 熔断器（连续失败停止重试） | 当前简化版不太会陷入循环，后续可加 | 按需补入 | Claude Code 的 circuit breaker |

---

## Claude Code 的做法（进阶阅读）

> 以下对照参考实现 `python/mini_claude/` 和原始文档 `docs/07-context.md`，第一遍可以跳过。

### 5 级 vs 4 层

Claude Code 有 5 级压缩流水线，我们简化成了 4 层：

| Claude Code | 我们 | 区别 |
|---|---|---|
| Level 1: 预算裁剪 + 磁盘持久化 | Layer 0+1: 截断 + Budget | Claude Code 对 >30KB 结果先存磁盘再给预览，我们直接截断 |
| Level 2: History Snip | Layer 2: Snip | 基本一致 |
| Level 3: Microcompact | 省略 | Claude Code 分两条路径（缓存冷/热），我们只实现了时间触发版 |
| Level 4: Context Collapse | 省略 | 投影式折叠，不修改原始消息，类似数据库 View |
| Level 5: Autocompact | Layer 3: Compact | Claude Code 用两阶段摘要 + 熔断器，我们用单段摘要 |

### Token 估算：锚点 + 粗估

我们直接用 `response.usage.input_tokens` 作为利用率依据——简单准确。

Claude Code 更复杂：用最近一次 API 返回的 `usage` 作为"锚点"，新增的消息用 `字符数 / 4` 粗估 token 数。这样在两次 API 调用之间也能估算当前利用率，把误差从纯估算的 30%+ 降到 <5%。

我们不需要这个的原因：我们的压缩管道总是在 API 调用前/后触发，此时刚好有最新的 `usage` 数据。

### 压缩与缓存的矛盾

API 提供商通常会缓存请求的前缀（KV cache）。如果多次请求的前面部分相同，缓存命中的 token 只收很低的价格（Anthropic 是正常价的 0.1x）。正常对话都是 append（往末尾加新消息），前面不变，所以缓存命中率很高。

但压缩管道会修改历史消息——Budget 截短旧 `tool_result`、Snip 把旧内容换成占位符。一旦中间某条消息变了，从那个位置开始往后的缓存全部失效：

```text
self.messages: [msg1, msg2, msg3(被改了), msg4, msg5, msg6]
                ├─ 缓存命中 ─┤  ├──── 缓存失效，按原价算 ────┤
```

Compact 更极端：压缩完成后，整个 `self.messages` 会被替换成 3 条新消息，所以下一轮正常对话基本会从新的短历史重新开始命中缓存。

但这里有个细节要分清：**摘要那一次 API 调用本身不一定是零缓存命中。** Claude Code 官方文档描述的做法是复用同一套 system prompt、tools 和历史消息，只是在末尾追加一条总结指令，所以摘要请求仍然能读到前缀缓存。真正重建缓存的是摘要完成之后的新短历史。

#### 可以优化但我们没做的事

**compact 用更便宜的模型**：摘要不需要深度推理，用 Haiku 级别的模型就够了。但我们的参考实现为了教学简单，仍然用当前对话模型（`self.model`），没有自动切换。

**compact 前后分层看缓存**：摘要请求可以尽量复用旧前缀；摘要完成后的后续请求则会基于新的短历史重新建缓存。这两段不要混在一起算。

#### Claude Code 的解决方案：cache_edits

Claude Code 用一个叫 `cache_edits` 的 Anthropic API 机制来解决"压缩 vs 缓存"的矛盾。

普通做法（我们的入门版）：想清掉 msg3 的旧 `tool_result`，直接改 `self.messages[2]`。本地消息变了，前缀变了，msg3 之后的缓存全部失效。

`cache_edits` 做法：不改 `self.messages`，而是告诉 Anthropic 服务端 "帮我把缓存里 msg3 的内容删掉"。服务端在缓存层面操作，本地消息不动，前缀不变，缓存不失效——但模型看到的上下文里 msg3 的内容已经被清了。

Claude Code 的 Microcompact 就是这么用的：缓存还热的时候（上次 API 调用不超过 5 分钟）用 `cache_edits` 在服务端删；缓存过期了就没必要保护它了，直接改本地消息。

#### DeepSeek 的情况

DeepSeek 有自动的 Context Caching（默认开启，不需要改代码），但它的 Anthropic 兼容接口[明确标注](https://api-docs.deepseek.com/guides/anthropic_api) `cache_control` 参数是 **Ignored（被忽略）**。也就是说：

- **自动缓存有效**：只要前缀不变，DeepSeek 会自动命中缓存
- **手动控制无效**：不能像 Anthropic 原生 API 那样标记缓存断点，也没有 `cache_edits`
- 所以 Claude Code 那套缓存感知的压缩策略在 DeepSeek 上用不了，我们的入门版也不做

### 两阶段摘要

Claude Code 的 auto-compact 用一个巧妙的提示词结构：

1. 先让模型在 `<analysis>` 块中推理（类似 chain-of-thought），分析对话中的关键信息
2. 再生成标准化的 `<summary>` 包含 9 个部分（目标、进度、决策、文件列表等）
3. 最后剥离 `<analysis>` 只保留 `<summary>` ——典型的"思考链草稿"技术

我们的单段摘要更简单，但信息密度不如两阶段版本。如果后续发现压缩后模型"失忆"严重，可以升级到两阶段。

### 原版 vs 入门版对比

| 维度 | 原版（参考实现） | 入门版 |
|---|---|---|
| 压缩层级 | 4 层（budget + snip + microcompact + compact） | 4 层（截断 + budget + snip + compact） |
| Token 计数 | API 返回值 | API 返回值 |
| Budget 触发 | 50%/70% 双阈值 | 50%/70% 双阈值 |
| Snip 策略 | 同文件去重 + 保留最近 3 个 | 同文件去重 + 保留最近 3 个 |
| Microcompact | 5 分钟空闲触发 | 省略（可作为练习补回） |
| Auto-compact | 单段摘要 | 单段摘要 |
| 大结果持久化 | 磁盘持久化（>30KB） | 省略（直接截断） |
| 手动压缩 | `/compact` 命令 | `/compact` 命令 |

---

> **下一章**：让 Agent 跨会话记住信息 — Memory 系统（`/memory` 命令 + CLAUDE.md 自动读取）。
