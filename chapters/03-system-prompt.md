# 3. System Prompt — 给 Agent 一本"员工手册"

本章参考代码：[examples/chapter-03/prompt.py](../examples/chapter-03/prompt.py) · [examples/chapter-03/agent.py](../examples/chapter-03/agent.py) · [examples/chapter-03/tools.py](../examples/chapter-03/tools.py)

## 本章目标

上一章你的 Agent 已经能读文件、写文件、跑命令了。但它的"人设"只有一句话：

```python
system="You are a helpful assistant"
```

同一个模型，换一段 system prompt，行为可以完全不同。试试分别给模型这三句话：

| System Prompt | 你说"我头疼" | 你说"帮我改个 bug" |
|---|---|---|
| 你是一个热心助手 | 建议你多喝水、早点休息 | 直接给一大段重构后的代码 |
| 你是一个专业医生 | 追问症状、持续时间、用药史 | "这不是我的专业领域" |
| 你是一个 coding agent | "这不是编程问题，我帮不了" | 先读文件，只改你要求的那一行 |

**同一个大脑，不同的人设，完全不同的反应。**

System Prompt 不改变模型的能力，但**塑造模型使用能力的方式**。

类比：你招了一个人（模型），给了他工具箱（tools.py）。System Prompt 就是他的**员工手册**——告诉他身份是什么、做事风格怎样、哪些工具优先用、哪些事情不要做。

对于 coding agent，system prompt 还需要解决几个特有问题：

| 没有这条规则 | 模型会怎样 |
|---|---|
| 没说"先读文件再改" | 根据函数名猜内容，猜错就改坏 |
| 没说"用 edit_file 别用 sed" | 默认用 shell 命令，绕过你定义的工具 |
| 没说"不要过度工程" | 你让它改个变量名，它顺手重构整个文件 |

本章会创建 `prompt.py`，从一句话升级到一份完整的员工手册，包含：

- 身份和行为规则（静态模板）
- 工作目录、日期、平台等运行时信息（动态上下文）
- 项目说明文件 CLAUDE.md 的内容（项目指令）
- Git 分支和最近提交（Git 上下文）

---

## 文件分工更新

加入 `prompt.py` 后，三个文件各管各的：

| 文件 | 负责什么 |
|---|---|
| `tools.py` | 定义工具 + 执行工具 |
| `prompt.py` | 构造 system prompt（模板 + 动态上下文） |
| `agent.py` | 调大模型 + 管对话循环（从 prompt.py 拿 system prompt，从 tools.py 拿工具） |

---

## Step 1：创建 prompt.py — 先写一个静态模板

> 文件：`prompt.py`（新文件，和 `agent.py`、`tools.py` 同目录）

先从最简单的开始——一个多行字符串。这是实际写进代码的英文版：

```python
SYSTEM_PROMPT_TEMPLATE = """\
You are Mini Claude Code, a lightweight coding assistant CLI.
You are an interactive agent that helps users with software engineering tasks.

# Doing tasks
 - The user will primarily request you to perform software engineering tasks. \
These may include solving bugs, adding new functionality, refactoring code, \
explaining code, and more.
 - In general, do not propose changes to code you haven't read. If a user asks \
about or wants you to modify a file, read it first.
 - Do not create files unless they're absolutely necessary. Prefer editing an \
existing file to creating a new one.
 - If an approach fails, diagnose why before switching tactics. Don't retry the \
identical action blindly, but don't abandon a viable approach after a single \
failure either.
 - Be careful not to introduce security vulnerabilities such as command injection, \
XSS, SQL injection, and other OWASP top 10 vulnerabilities.
 - Avoid over-engineering. Only make changes that are directly requested or \
clearly necessary.
   - Don't add features, refactor code, or make "improvements" beyond what was \
asked. A bug fix doesn't need surrounding code cleaned up.
   - Don't add error handling, fallbacks, or validation for scenarios that can't \
happen. Only validate at system boundaries (user input, external APIs).
   - Don't create helpers, utilities, or abstractions for one-time operations. \
Three similar lines of code is better than a premature abstraction.

# Executing actions with care
Carefully consider the reversibility and blast radius of actions. Generally you \
can freely take local, reversible actions like editing files or running tests. \
But for actions that are hard to reverse, affect shared systems beyond your local \
environment, or could otherwise be risky or destructive, check with the user \
before proceeding.

Examples of risky actions that warrant user confirmation:
- Destructive operations: deleting files/branches, rm -rf, overwriting uncommitted changes
- Hard-to-reverse operations: force-pushing, git reset --hard, removing packages
- Actions visible to others: pushing code, creating/commenting on PRs or issues

When you encounter an obstacle, do not use destructive actions as a shortcut. \
Investigate before deleting or overwriting, as it may represent the user's \
in-progress work.

# Using your tools
 - Do NOT use run_shell to run commands when a relevant dedicated tool is provided:
   - To read files use read_file instead of cat, head, tail
   - To edit files use edit_file instead of sed or awk
   - To create files use write_file instead of echo redirection
   - To search for files use list_files instead of find or ls
   - To search file content use grep_search instead of grep or rg
   - Only use run_shell for system commands that no dedicated tool can handle.
 - You can call multiple tools in a single response. If there are no dependencies \
between them, make all independent tool calls in parallel.

# Tone and style
 - Your responses should be short and concise.
 - Only use emojis if the user explicitly requests it.

# Output efficiency
Go straight to the point. Lead with the answer or action, not the reasoning. \
Skip filler words and preamble. Do not restate what the user said — just do it.

If you can say it in one sentence, don't use three.

# Environment
Working directory: {{cwd}}
Date: {{date}}
Platform: {{platform}}
Shell: {{shell}}
{{git_context}}
{{claude_md}}"""
```

下面是同一份模板的中文逐段翻译，帮你理解每段在说什么（不需要写进代码）：

```text
你是 Mini Claude Code，一个轻量级编程助手 CLI。        ← 身份
你是一个帮用户做软件工程任务的交互式 agent。

# 做事规则
 - 用户主要让你做软件工程任务：修 bug、加功能、重构、解释代码
 - 没读过的代码不要乱改，先读文件
 - 不要乱建新文件，优先改已有文件
 - 一种方法失败了，先分析原因再换方案，不要盲目重试
 - 注意安全漏洞：命令注入、XSS、SQL 注入等                ← 安全意识
 - 不要过度工程，只做用户要求的修改
   - 不要加功能、不要重构、bug fix 不需要顺手清理
   - 不可能发生的场景不要加 try-catch
   - 三行相似代码 > 一个过早的抽象                    ← 反模式接种

# 谨慎执行操作
仔细考虑操作的可逆性和爆炸半径。本地可逆操作（改文件、跑测试）
可以放心做；但难以撤销、影响共享环境、有破坏性的操作，先问用户。
                                                       ← 爆炸半径框架
危险操作示例：
- 删除文件/分支、rm -rf、覆盖未提交的改动
- force push、git reset --hard、删包
- push 代码、创建/评论 PR

遇到阻碍时不要用破坏性操作走捷径，先调查再动手。

# 工具偏好
 - 有专用工具就不要用 run_shell：
   - 读文件用 read_file，别用 cat/head/tail
   - 改文件用 edit_file，别用 sed/awk
   - 建文件用 write_file，别用 echo 重定向
   - 列目录用 list_files，别用 find/ls
   - 搜内容用 grep_search，别用 grep/rg
   - run_shell 只用于没有专用工具能做的系统命令          ← 工具偏好映射
 - 多个工具调用之间没有依赖时，并行调用                   ← 效率

# 输出风格
 - 回复要简短
 - 不要用 emoji，除非用户要求

# 输出效率
先给结论，再说理由。不要废话、不要重复用户说的话。
能一句话说完就不要三句。                               ← 简洁直接

# 运行环境
工作目录: {{cwd}}                                     ← 以下全是动态替换
日期: {{date}}
平台: {{platform}}
Shell: {{shell}}
{{git_context}}
{{claude_md}}
```

### Python 语法：三引号多行字符串

```python
text = """\
第一行
第二行"""
```

- `"""..."""`：三引号允许字符串跨多行，中间可以包含换行符
- 开头的 `\`：紧跟 `"""` 后面的 `\` 意思是"第一行不要空行"。如果不加，字符串会以一个空行开头

### 模板里的 `{{placeholder}}`

`{{cwd}}`、`{{date}}` 这些**不是 Python 语法**，只是普通文本。它们是我们自己定义的占位符，后面会用字符串替换把它们换成真实值。

用双花括号 `{{}}` 而不是单花括号 `{}`，是为了避免和 Python 的 f-string 冲突。

### 模板里的关键规则

这些规则来自 Claude Code 真实的 system prompt，经过 A/B 测试验证有效。模板分成 6 段，每段解决不同的问题：

**Doing tasks — 做事规则（6 条独立规则 + 1 条伞状规则）**

前 5 条是独立规则，各管一类常见错误：

| 规则 | 没有这条会怎样 |
|---|---|
| 先读再改 | 根据函数名猜测代码内容然后直接改，猜错就搞坏 |
| 不要乱建文件 | 每次都新建文件而不是改已有的，项目越来越臃肿 |
| 失败先诊断 | 一种方法报错就换另一种，没理解根本原因，反复踩坑 |
| 注意安全漏洞 | 拼接 SQL、不转义用户输入，引入注入漏洞 |

最后一条"不要过度工程"是总则，带三个子项堵住具体漏洞：

| 子项 | 堵住什么漏洞 | 没有这条会怎样 |
|---|---|---|
| Don't add features, refactor code, or make "improvements" beyond what was | 范围蔓延 | 你让它改个变量名，它顺手重构整个文件 |
| Don't add error handling... | 防御性编程 | 给不可能出错的内部调用加 try-catch |
| Don't create helpers... | 过早抽象 | 看到两行相似代码就提取成 helper 函数 |

总则"Avoid over-engineering"本身太模糊，模型可以自我合理化——"我加的 type hints 让代码更好了，这不算过度工程"。三个子项把具体场景堵死了。

**Executing actions with care — 爆炸半径框架**

这段教模型一个**评估框架**而不是给一张"禁止操作"清单：

```text
可逆性 × 影响范围 = 风险等级

低风险 = 可逆 + 只影响本地（编辑文件、跑测试）    → 放心做
高风险 = 难撤销 + 影响共享环境（force push、删分支） → 先问用户
```

这比穷举规则扩展性强——模型遇到规则列表之外的新场景也能自行推理。

**Using tools — 工具偏好映射**

没有这段，模型会默认用训练数据中最常见的方式——`cat`、`sed`、`grep` 等 shell 命令。而你定义的专用工具有更好的控制力（比如 `edit_file` 会检查唯一性，`grep_search` 会限制输出长度）。

"并行调用"那条也很重要：如果模型要同时读三个文件，可以一次发三个工具调用而不是一个一个来。

**Output efficiency — 输出效率**

没有这段，模型会先复述你的问题（"你让我改 xxx，我来看看..."），再给一堆解释，最后才给结论。加了这段后，模型直接给结果。

### 怎么验证规则有没有用：A/B 测试

"经过 A/B 测试"的核心方法很简单：**同一个任务，有规则跑一遍，没规则跑一遍，比较结果。**

```text
1. 找 3-5 个会触发"坏行为"的任务（如"把变量 f 改成 file"）
2. A 组：system prompt 不含这条规则
   B 组：system prompt 含这条规则
3. 每组各跑 3 次（模型有随机性），人工判断"是否只改了要求的部分"
4. 留下有效的规则，删掉没效果的
```

关键原则：**每次只测一条规则**，否则分不清哪条在起作用。

通过这种方法会发现一些反直觉的事：

- **模糊的正面指令没用**："write clean code" 对行为影响接近零
- **具体的负面指令有用**："Don't add docstrings to code you didn't change" 立竿见影
- **措辞细节有影响**："avoid" < "don't" < "never"，约束力递增

这就是为什么 Claude Code 的 system prompt 读起来像被打磨过——每个词都是测出来的。

### 工具偏好映射

```text
Use read_file instead of cat/head/tail
Use edit_file instead of sed/awk
```

没有这段，模型会默认用训练数据中最常见的方式——`cat`、`sed`、`grep` 等 shell 命令。而你定义的专用工具有更好的控制力（比如 `edit_file` 会检查唯一性，`grep_search` 会限制输出长度）。

---

## Step 2：加入动态上下文

> 文件：`prompt.py`

模板里的 `{{cwd}}` 等占位符需要在运行时替换成真实值。写一个函数：

```python
import os
import platform
from datetime import date


def build_system_prompt():
    replacements = {
        # 当前工作目录，模型需要它才能给出正确的文件路径
        "{{cwd}}": os.getcwd(),
        # 今天日期，格式 "2026-06-23"，模型不知道今天几号
        "{{date}}": date.today().isoformat(),
        # 操作系统 + CPU架构，如 "Darwin arm64"，模型据此给平台相关命令
        "{{platform}}": f"{platform.system()} {platform.machine()}",
        # 用户的 shell，取环境变量 SHELL，没有就用 /bin/sh 兜底
        "{{shell}}": os.environ.get("SHELL", "/bin/sh"),
        # 后面 Step 会实现，先用空字符串占位，保证每一步都能跑
        "{{git_context}}": "",
        "{{claude_md}}": "",
    }
    result = SYSTEM_PROMPT_TEMPLATE
    for key, value in replacements.items():
        result = result.replace(key, value)
    return result
```

### 单独测试

先不接 `agent.py`，直接跑一下看看结果：

```bash
cd examples/chapter-03
python -c "from prompt import build_system_prompt; print(build_system_prompt())"
```

如果看到模板中的 `{{cwd}}` 被替换成了真实路径、`{{date}}` 变成了今天的日期，说明替换逻辑正确。

---

## Step 3：加载 CLAUDE.md

> 文件：`prompt.py`

### CLAUDE.md 是什么

CLAUDE.md 是放在项目目录里的说明文件，专门给 AI 读的。类似 `.eslintrc` 告诉 linter 规则，CLAUDE.md 告诉 AI 这个项目的特殊要求。

新建此项目的CLAUDE.md：

```text
默认中文
默认只检查、解释和更新教程，不直接修改学习代码
```

### 为什么要向上遍历

你可能在子目录里启动 Agent：

```text
~/project/src/api/  ← 你在这里
~/project/CLAUDE.md ← CLAUDE.md 在项目根目录
```

如果只读当前目录，就会错过项目根目录的 CLAUDE.md。所以需要从当前目录一路往上找，直到磁盘根目录。

### 实现

```python
from pathlib import Path


def load_claude_md():
    parts = []
    # Path.cwd() = 当前目录，.resolve() 转成绝对路径（去掉 .. 之类的）
    d = Path.cwd().resolve()
    while True:
        # Path 对象用 / 拼接路径，等价于 os.path.join(d, "CLAUDE.md")
        f = d / "CLAUDE.md"
        if f.is_file():
            try:
                # insert(0, ...) 插到列表开头，让上层目录的内容排在前面
                parts.insert(0, f.read_text())
            except Exception:
                pass
        parent = d.parent
        # 到磁盘根目录时 parent == d（/ 的 parent 还是 /），停止
        if parent == d:
            break
        d = parent
    if not parts:
        return ""
    return "\n\n# Project Instructions (CLAUDE.md)\n" + "\n\n---\n\n".join(parts)
```

### 把 load_claude_md 接入 build_system_prompt

修改 `build_system_prompt()` 中的一行：

```python
"{{claude_md}}": load_claude_md(),
```

---

## Step 4：加入 Git 上下文

> 文件：`prompt.py`

模型知道你在哪个分支、最近改了什么，就能更好地理解你的请求：

```python
import subprocess


def get_git_context():
    try:
        opts = {"encoding": "utf-8", "timeout": 3, "capture_output": True}
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], **opts
        ).stdout.strip()
        log = subprocess.run(
            ["git", "log", "--oneline", "-5"], **opts
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--short"], **opts
        ).stdout.strip()
        result = f"\nGit branch: {branch}"
        if log:
            result += f"\nRecent commits:\n{log}"
        if status:
            result += f"\nGit status:\n{status}"
        return result
    except Exception:
        return ""
```

### 为什么整个函数包在 try/except 里

如果当前目录不是 git 仓库，`git rev-parse` 会报错。用 try/except 捕获后返回空字符串——没有 git 信息不影响 Agent 正常工作。

### **opts 是什么

上面的代码跑了三条 git 命令（`rev-parse` 拿分支、`log` 拿提交、`status` 拿改动），它们都需要同样的三个参数。如果不提取，每条都要写一遍：

```python
branch = subprocess.run(
    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
    encoding="utf-8", timeout=3, capture_output=True,  # ← 重复
).stdout.strip()
log = subprocess.run(
    ["git", "log", "--oneline", "-5"],
    encoding="utf-8", timeout=3, capture_output=True,  # ← 重复
).stdout.strip()
status = subprocess.run(
    ["git", "status", "--short"],
    encoding="utf-8", timeout=3, capture_output=True,  # ← 重复
).stdout.strip()
```

提取成字典 + `**` 解包，就消除了重复：

```python
opts = {"encoding": "utf-8", "timeout": 3, "capture_output": True}
subprocess.run(["git", "rev-parse", ...], **opts)  # **opts 展开成上面三个参数
subprocess.run(["git", "log", ...], **opts)
subprocess.run(["git", "status", ...], **opts)
```

`**opts` 是 Python 的字典解包语法，把字典里的键值对展开成函数的关键字参数。

### 接入 build_system_prompt

```python
"{{git_context}}": get_git_context(),
```

---

## Step 5：接入 agent.py

> 文件：`agent.py`

改动只有两处：

**1. 修改导入**

之前：

```python
from tools import tool_definitions, execute_tool
```

改成：

```python
from tools import tool_definitions, execute_tool
from prompt import build_system_prompt
```

**2. 把 `build_system_prompt()` 提到循环外，替换硬编码的 system prompt**

system prompt 在整个会话中不会变，没必要每次 API 调用都重新构建。在外层循环之前调一次，存到变量里：

之前：

```python
while True:
    user_input = input("> ")
    ...
        response = client.messages.create(
            ...
            system="You are a helpful assistant",
            ...
        )
```

改成：

```python
system_prompt = build_system_prompt()

while True:
    user_input = input("> ")
    ...
        response = client.messages.create(
            ...
            system=system_prompt,
            ...
        )
```

---

## Step 6：测试

```bash
python agent.py
```

试这几个对话：

**测试 1：工具偏好**

```text
> 帮我看看当前目录有什么文件
```

模型应该调用 `list_files`，而不是 `run_shell("ls")`。这就是工具偏好映射在起作用。

**测试 2：不过度工程**

```text
> 读一下 tools.py，把 read_file 函数的变量 f 改成 file
```

模型应该只改你要求的那个变量名，而不是顺手把整个文件重构。

**测试 3：动态上下文**

```text
> 今天是几号？我在哪个目录？
```

模型应该能回答正确的日期和工作目录——这些信息来自 system prompt 里的 Environment 部分。

---

## 回头看：System Prompt 的三层结构

写完 `prompt.py`，你可能会发现里面的内容天然分成三层。这不是巧合——Claude Code 也是这么组织的：

| 层 | 什么时候变 | 对应代码 | 举例 |
|---|---|---|---|
| **静态模板** | 几乎不变（你改代码才变） | `SYSTEM_PROMPT_TEMPLATE` 里的文字部分 | 身份、做事规则、工具偏好、输出风格 |
| **动态上下文** | 每次启动都不同 | `build_system_prompt()` 里的替换 | cwd、日期、平台、shell、git 分支 |
| **项目指令** | 换个项目就不同 | `load_claude_md()` | CLAUDE.md 里的项目规则 |

### 为什么要分三层

**不分层的写法**：把所有东西硬编码在一个字符串里——

```python
system = """You are a coding assistant.
Working directory: /Users/you/project
Date: 2026-06-23
This project uses Python 3.13...
"""
```

问题很多：换个目录要改代码、换个项目要改代码、明天日期就错了。

**分层的好处**：

- **静态模板**是你打磨好的"员工手册"，写一次到处用。不管什么项目、什么目录，做事规则都一样
- **动态上下文**自动获取，不需要手动更新。今天跑就是今天的日期，换个目录就是新的路径
- **项目指令**跟着项目走。A 项目的 CLAUDE.md 说"用中文回复"，B 项目的说"code review 要严格"——同一个 Agent，不同的项目规范

### 后续章节会加入更多层

现在模板末尾只有 `{{git_context}}` 和 `{{claude_md}}` 两个动态占位符。后续章节还会加入：

| 占位符 | 来源 | 章节 |
|---|---|---|
| `{{memory}}` | 记忆系统——跨会话记住用户偏好 | Ch8 |
| `{{skills}}` | 技能描述——告诉模型有哪些可用技能 | Ch9 |
| `{{agents}}` | 子 Agent 描述——可以派出的专家 | Ch11 |

这些都是**动态内容**，放在模板末尾是刻意的——原因见进阶阅读"7 层递进结构"。

---

## prompt.py 完整代码

自己先写，卡住再看：

```python
import os
import platform
import subprocess
from datetime import date
from pathlib import Path

SYSTEM_PROMPT_TEMPLATE = """\
You are Mini Claude Code, a lightweight coding assistant CLI.
You are an interactive agent that helps users with software engineering tasks.

# Doing tasks
 - The user will primarily request you to perform software engineering tasks. \
These may include solving bugs, adding new functionality, refactoring code, \
explaining code, and more.
 - In general, do not propose changes to code you haven't read. If a user asks \
about or wants you to modify a file, read it first.
 - Do not create files unless they're absolutely necessary. Prefer editing an \
existing file to creating a new one.
 - If an approach fails, diagnose why before switching tactics. Don't retry the \
identical action blindly, but don't abandon a viable approach after a single \
failure either.
 - Be careful not to introduce security vulnerabilities such as command injection, \
XSS, SQL injection, and other OWASP top 10 vulnerabilities.
 - Avoid over-engineering. Only make changes that are directly requested or \
clearly necessary.
   - Don't add features, refactor code, or make "improvements" beyond what was \
asked. A bug fix doesn't need surrounding code cleaned up.
   - Don't add error handling, fallbacks, or validation for scenarios that can't \
happen. Only validate at system boundaries (user input, external APIs).
   - Don't create helpers, utilities, or abstractions for one-time operations. \
Three similar lines of code is better than a premature abstraction.

# Executing actions with care
Carefully consider the reversibility and blast radius of actions. Generally you \
can freely take local, reversible actions like editing files or running tests. \
But for actions that are hard to reverse, affect shared systems beyond your local \
environment, or could otherwise be risky or destructive, check with the user \
before proceeding.

Examples of risky actions that warrant user confirmation:
- Destructive operations: deleting files/branches, rm -rf, overwriting uncommitted changes
- Hard-to-reverse operations: force-pushing, git reset --hard, removing packages
- Actions visible to others: pushing code, creating/commenting on PRs or issues

When you encounter an obstacle, do not use destructive actions as a shortcut. \
Investigate before deleting or overwriting, as it may represent the user's \
in-progress work.

# Using your tools
 - Do NOT use run_shell to run commands when a relevant dedicated tool is provided:
   - To read files use read_file instead of cat, head, tail
   - To edit files use edit_file instead of sed or awk
   - To create files use write_file instead of echo redirection
   - To search for files use list_files instead of find or ls
   - To search file content use grep_search instead of grep or rg
   - Only use run_shell for system commands that no dedicated tool can handle.
 - You can call multiple tools in a single response. If there are no dependencies \
between them, make all independent tool calls in parallel.

# Tone and style
 - Your responses should be short and concise.
 - Only use emojis if the user explicitly requests it.

# Output efficiency
Go straight to the point. Lead with the answer or action, not the reasoning. \
Skip filler words and preamble. Do not restate what the user said — just do it.

If you can say it in one sentence, don't use three.

# Environment
Working directory: {{cwd}}
Date: {{date}}
Platform: {{platform}}
Shell: {{shell}}
{{git_context}}
{{claude_md}}"""


def load_claude_md():
    parts = []
    d = Path.cwd().resolve()
    while True:
        f = d / "CLAUDE.md"
        if f.is_file():
            try:
                parts.insert(0, f.read_text())
            except Exception:
                pass
        parent = d.parent
        if parent == d:
            break
        d = parent
    if not parts:
        return ""
    return "\n\n# Project Instructions (CLAUDE.md)\n" + "\n\n---\n\n".join(parts)


def get_git_context():
    try:
        opts = {"encoding": "utf-8", "timeout": 3, "capture_output": True}
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], **opts
        ).stdout.strip()
        log = subprocess.run(
            ["git", "log", "--oneline", "-5"], **opts
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--short"], **opts
        ).stdout.strip()
        result = f"\nGit branch: {branch}"
        if log:
            result += f"\nRecent commits:\n{log}"
        if status:
            result += f"\nGit status:\n{status}"
        return result
    except Exception:
        return ""


def build_system_prompt():
    replacements = {
        "{{cwd}}": os.getcwd(),
        "{{date}}": date.today().isoformat(),
        "{{platform}}": f"{platform.system()} {platform.machine()}",
        "{{shell}}": os.environ.get("SHELL", "/bin/sh"),
        "{{git_context}}": get_git_context(),
        "{{claude_md}}": load_claude_md(),
    }
    result = SYSTEM_PROMPT_TEMPLATE
    for key, value in replacements.items():
        result = result.replace(key, value)
    return result
```

---

## 本章完成检查

- [ ] `prompt.py`：SYSTEM_PROMPT_TEMPLATE 模板 + `build_system_prompt()` 函数
- [ ] `prompt.py`：`load_claude_md()` 向上遍历加载 CLAUDE.md
- [ ] `prompt.py`：`get_git_context()` 获取 Git 信息
- [ ] `agent.py`：`system=build_system_prompt()` 替换硬编码字符串
- [ ] 模型使用 `list_files` 而不是 `run_shell("ls")` 来列目录
- [ ] 模型能正确回答当前日期和工作目录

---

## 本章暂时省略的能力

| 省略了什么 | 为什么省略 | 哪一章补回 | 补回后对齐参考实现的哪个能力 |
|---|---|---|---|
| `@include` 语法（CLAUDE.md 中引用其他文件） | 核心流程不依赖它，加进来会同时引入正则表达式、递归、循环引用检测 | Ch4 CLI / Session 或作为本章进阶练习 | `prompt.py` 的 `_resolve_includes()` |
| `.claude/rules/*.md` 自动加载 | 同上，属于配置系统增强 | 同上 | `prompt.py` 的 `_load_rules_dir()` |
| memory / skills / agents 占位符 | 这些模块还没实现 | Ch8 Memory / Ch9 Skills / Ch11 Sub-agent | `build_system_prompt()` 中的 `{{memory}}`、`{{skills}}`、`{{agents}}` |
| System 段（工具权限模式、tags、hooks） | 需要权限系统配合才有意义 | Ch6 Permissions | SYSTEM_PROMPT_TEMPLATE 中的 "# System" 段落 |

---

## Claude Code 的做法（进阶阅读）

> 以下对照原项目参考实现 `python/mini_claude_/prompt.py`，第一遍可以跳过。

### 7 层递进结构

Claude Code 的 system prompt 从抽象到具体分 7 层：

```text
1. Identity      → 我是谁（coding assistant CLI）
2. System        → 运行环境的基本事实（工具权限、标签系统）
3. Doing Tasks   → 怎么写代码（反模式接种：3 条精确的"不要"）
4. Actions       → 哪些操作需要确认（爆炸半径框架：可逆性 x 影响范围）
5. Using Tools   → 哪个工具优先（偏好映射表）
6. Tone & Style  → 输出格式（简洁、不用 emoji、引用时带行号）
7. Output        → 怎么更精炼（先结论后推理）
```

**顺序是刻意的。** 模型处理 system prompt 时，先读到的概念会成为理解后续内容的框架：

- **先 Identity 后 Rules**：模型先知道"我是 coding agent"，再读到"不要过度工程"时，它会在 coding 的语境下理解这条规则。如果反过来，先给规则再说身份，模型对规则的理解可能偏泛化
- **先 Rules 后 Style**：做事规则比格式要求重要。如果模型在上下文窗口快满时截断了末尾，丢掉的是格式偏好而不是核心行为规则
- **动态内容放最后**：Environment、CLAUDE.md、memory 等放末尾，利用近因效应——最后读到的信息在模型推理时权重更高

我们的版本已经覆盖了 7 层中的 6 层（Identity → Doing Tasks → Actions → Using Tools → Tone & Style → Output），只缺 System 段（工具权限模式、标签系统、hooks），那个需要 Ch6 权限系统实现后才有意义。

### 我们的模板和真实 Claude Code 的关系

我们写的模板**不是** Claude Code system prompt 的 100% 复制，而是忠实的浓缩版：

| 维度 | 真实 Claude Code | 参考实现 (mini_claude_) | 我们的 beginner 版 |
|---|---|---|---|
| 总长度 | ~3000 词 | ~800 词 | ~400 词 |
| 结构 | 7 段 + 大量子项 | 完整 7 段 | 6 段（缺 System） |
| 规则数 | 30+ 条 | 20+ 条 | ~15 条 |
| 动态占位符 | 10 个 | 10 个 | 6 个 |

按段落对比：

| 段落 | Claude Code | 我们（当前） | 差距 |
|---|---|---|---|
| Identity | 2 行 | 2 行 | 一致 |
| System | 5 条（工具权限、标签、hooks） | 无 | Ch6 补 |
| Doing Tasks | 10+ 条 | 9 条 | 核心覆盖 |
| Actions | 完整爆炸半径框架 | 爆炸半径框架 + 示例 | 核心覆盖 |
| Using Tools | 6 条 + 并行 + agent | 6 条 + 并行 | 接近一致 |
| Tone & Style | 4 条 | 2 条 | 够用 |
| Output | 单独一段 | 单独一段 | 核心覆盖 |

参考实现保留了全部结构和核心规则，砍掉的主要是会话管理细节（scratchpad 目录、context 压缩提示）、git 工作流指导（PR 创建模板、commit 格式）等边缘场景。我们的 beginner 版与参考实现差距已经很小，主要缺 System 段（依赖 Ch6 权限系统）。

### 反模式接种

**告诉模型"不要做什么"比只说"要做什么"有效得多。**

正面指令（"be concise"）留下了自我合理化空间——模型会觉得"加注释是让代码更易读的"，然后给每个函数写 docstring。

负面指令（"Don't add docstrings to code you didn't change"）消除了解释余地。

### 爆炸半径框架

我们的模板已经包含了这个框架的核心——"Executing actions with care" 段落。它不是列一张"禁止操作"清单，而是教模型一个二维评估模型：**可逆性 x 影响范围**。

- 低风险 = 可逆 + 只影响本地（编辑文件）
- 高风险 = 不可逆 + 影响共享环境（force push、删除云资源）

这比穷举规则扩展性强——模型遇到规则列表之外的新场景也能自行推理。Ch6 权限系统会在代码层面实现这个框架（高风险操作自动弹确认），目前的 prompt 规则已经让模型知道应该"先问再做"。

### @include 和 .claude/rules/

参考实现的 CLAUDE.md 支持 `@./path` 语法引用其他文件，还会自动加载 `.claude/rules/*.md` 下的规则文件。

实现涉及正则匹配、递归解析、循环引用检测（visited set）和最大深度限制。这些是配置系统的增强，不影响核心 Agent 功能。

---

> **下一章**：有了工具和人设，下一步让 Agent 变得可交互——CLI 入口、REPL 命令和会话持久化。
