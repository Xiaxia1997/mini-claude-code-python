<div align="center">

# Mini Claude Code in Python

**不用 Agent 框架，从 30 多行 Python 开始，逐章写出一个 Mini Claude Code。**

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![DeepSeek API](https://img.shields.io/badge/DeepSeek_API-low--cost_ready-4D6BFE?style=flat-square)](https://api-docs.deepseek.com/guides/anthropic_api)
[![Progress](https://img.shields.io/badge/Progress-Chapter_2_complete-22C55E?style=flat-square)](./chapters/02-tools.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg?style=flat-square)](./LICENSE)

[开始阅读](./chapters/01-agent-loop.md) ·
[查看参考代码](./examples/) ·
[原项目与致谢](#来源与致谢)

</div>

---

**Claude Code 能读代码、改文件、跑命令，还能连续完成多步任务。它到底是怎么工作的？**

读一百遍各种解读，不如自己动手实践。

这个项目：**不用 LangChain、LangGraph 等 Agent 框架，从最小 Python 实现开始，逐章写出一个Code Agent**

```text
多轮对话 → 工具调用 → 会话 → 流式输出 → 权限
         → 上下文压缩 → 记忆 → Skills → Sub-Agent → MCP
```

每一步都会讲清楚：

- **Claude Code 面临什么问题**：为什么仅仅调用一次 LLM 还不够？
- **最小版本如何实现**：先用少量 Python 跑通关键消息流。
- **生产级实现还需要什么**：并行执行、安全边界、恢复和上下文控制。

> 这不是 Claude Code 官方源码的复刻，也不是已经完成的生产级替代品；它是一份随代码逐章演进的学习型实现。

## 没有 Claude API，也能低成本跑起来

理解 Claude Code 的消息循环，不一定要先承担高额 API 试错成本。

DeepSeek 官方提供了 **Anthropic API 兼容接口**。当前代码直接使用 Anthropic Python SDK，只需要换成 DeepSeek 的 `base_url` 和 API Key，就可以练习：

- `messages` 多轮历史
- thinking / text block 处理
- `tool_use → tool_result` 回传
- Agent Loop 的继续与停止条件

```python
client = anthropic.Anthropic(
    base_url="https://api.deepseek.com/anthropic",
    api_key=os.environ["DEEPSEEK_API_KEY"],
)
```

这意味着你可以先用 **DeepSeek 的低成本 API** 把 Claude Code 式消息流真正跑通，再决定是否切换到 Claude 模型测试更完整的行为表现。

> [!NOTE]
> API 格式兼容不等于模型能力、工具行为和 Claude Code 产品体验完全一致。本项目用它降低学习和调试成本，而不是把 DeepSeek 宣传成 Claude 的等价替代。

[DeepSeek Anthropic API 官方文档](https://api-docs.deepseek.com/guides/anthropic_api) ·
[查看实时价格](https://api-docs.deepseek.com/quick_start/pricing)

## 现在已经能跑什么？

当前 `main` 是一份**随教程逐章演进的实现**：

- ✅ **Chapter 1 · Agent Loop**：API 调用、多轮输入、消息历史、thinking block 过滤<br>
  [阅读教程](./chapters/01-agent-loop.md) · [查看 `agent.py`](./examples/chapter-01/agent.py)
- ✅ **Chapter 2 · Tools**：`read_file`、工具结果回传和两层 Agent Loop<br>
  [阅读教程](./chapters/02-tools.md) · [查看 `agent.py`](./examples/chapter-02/agent.py) · [查看 `tools.py`](./examples/chapter-02/tools.py)

```bash
> 我叫小明
你好，小明！

> 我叫什么？
你叫小明。
```

模型并没有突然获得记忆。程序只是把完整的 `messages` 历史在每一轮重新发给它——这正是 Claude Code 这类 Coding Agent 最基础的一层上下文机制。


## 学习路线

| 章节 | 对应的 Claude Code 核心问题 | 状态 |
|---|---|:---:|
| [01 · Agent Loop](./chapters/01-agent-loop.md) | 多轮对话为什么能“记住”上文？ | ✅ |
| [02 · Tools](./chapters/02-tools.md) | 模型如何从“会说”变成“会做”？ | ✅ |
| 03 · System Prompt | Agent 如何知道身份、规则和工作目录？ | 计划中 |
| 04 · CLI & Session | 对话如何保存、恢复和中断？ | 计划中 |
| 05 · Streaming | 如何边生成、边显示、边执行？ | 计划中 |
| 06 · Permissions | 如何避免 Agent 随意执行危险操作？ | 计划中 |
| 07 · Context | 消息越来越长后如何压缩？ | 计划中 |
| 08 · Memory | 什么信息值得跨会话保留？ | 计划中 |
| 09 · Skills | 如何按需加载可复用工作流？ | 计划中 |
| 10 · Plan Mode | 如何只规划、不修改文件？ | 计划中 |
| 11 · Sub-Agent | 如何拆分任务并隔离上下文？ | 计划中 |
| 12 · MCP | 如何连接外部工具服务器？ | 计划中 |

每完成一个阶段都会发布 tag：

- [`v0.1-agent-loop`](https://github.com/Xiaxia1997/mini-claude-code-python/tree/v0.1-agent-loop)：多轮消息历史
- [`v0.2-tools`](https://github.com/Xiaxia1997/mini-claude-code-python/tree/v0.2-tools)：`read_file` 与完整工具循环

## 不是只看代码，而是理解设计

不是只贴一份最终代码。每章会同时回答四件事：

1. **Claude Code 为什么需要这一层**：缺少它时，长任务会在哪里失效？
2. **消息怎么流动**：模型、Harness 和工具分别拿到了什么？
3. **最小代码怎么写**：先跑通核心路径，再理解生产级复杂度。
4. **常见坑是什么**：例如 `response.content[0]` 为什么不一定是文本。

## 快速开始

需要 Python 3.11 或更高版本，以及一个 DeepSeek API Key。

```bash
git clone https://github.com/Xiaxia1997/mini-claude-code-python.git
cd mini-claude-code-python

python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .

export DEEPSEEK_API_KEY="your-api-key"
cd examples/chapter-02
python agent.py
```

输入 `exit` 结束对话。

> [!IMPORTANT]
> 当前示例默认调用 DeepSeek 的 Anthropic 兼容接口。代码通过环境变量读取 API Key，不要把真实密钥写进代码、教程或 Git 历史。

## 项目结构

```text
mini-claude-code-python/
├── chapters/               # 逐章教程：原理、消息流、代码与常见错误
│   ├── 01-agent-loop.md
│   └── 02-tools.md
├── examples/               # 每章独立、可运行的完整参考代码
│   ├── chapter-01/
│   │   └── agent.py
│   └── chapter-02/
│       ├── agent.py
│       └── tools.py
└── tests/                  # 编译、链接、版权与密钥检查
```

## 这份教程适合谁？

- 正在使用 Claude Code，但想理解它为什么能连续读写代码的人
- 想学习 Tool Use，却不想一开始就被框架抽象淹没的人
- 读过 Agent 概念，但还没有亲手处理 `tool_use → tool_result` 的 Python 开发者

## 来源与致谢

本项目的学习路径与实现思路受到 [claude-code-from-scratch](https://github.com/Windy3f3f3f3f/claude-code-from-scratch) 启发。

在跟随原教程实践的过程中，我重新组织了讲解顺序，并记录自己的实现、验证过程与理解。本仓库不是原项目的官方版本。

感谢 [@Windy3f3f3f3f](https://github.com/Windy3f3f3f3f) 及原项目贡献者的开源工作。

## License

[MIT](./LICENSE)
