<div align="center">

# Mini Claude Code in Python

**从一次 LLM 调用开始，渐进式构建一个 Coding Agent**

</div>

本仓库不是生产级 Agent 框架，而是一份随代码逐章演进的实现笔记。

我们会从最小的多轮对话开始，逐步加入工具调用、System Prompt、会话管理、上下文压缩、记忆和 Sub-Agent。每章包含：

- 原理拆解：这一层机制解决什么问题
- 最小实现：先跑通核心路径，再增加工程能力
- 消息流：模型、工具和程序之间传递了什么
- 常见错误：哪些写法看起来合理，却会让 Agent Loop 断掉

## 当前进度

- [x] Chapter 1：Agent Loop 与多轮消息历史
- [ ] Chapter 2：工具定义、执行与 tool result 回传

代码会和教程同步演进。当前 `main` 展示最新进度；每完成一个阶段后会增加对应 tag。

## 章节

1. [Agent Loop：从一次 API 调用到多轮对话](./chapters/01-agent-loop/)
2. [工具系统：让模型从“会说”变成“会做”](./chapters/02-tools/)

## 当前结构

```text
mini-claude-code-python/
├── chapters/
│   ├── 01-agent-loop/
│   └── 02-tools/
├── src/
│   └── mini_claude/
│       ├── agent.py
│       └── tools.py
└── tests/
```

## 快速开始

需要 Python 3.11 或更高版本。

```bash
git clone https://github.com/Xiaxia1997/mini-claude-code-python.git
cd mini-claude-code-python

python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .

export DEEPSEEK_API_KEY="your-api-key"
python src/mini_claude/agent.py
```

输入 `exit` 结束对话。

> 代码通过环境变量读取 API Key。不要把真实密钥写进代码、教程或 Git 历史。

## 这份教程适合谁

- 想理解 Code Agent 内部消息流，而不只会调用框架的人
- 想从 Tool Use 入手理解 Agent Loop 的 Python 开发者
- 读过 Agent 概念，但还没亲手把循环跑起来的人

## 来源与致谢

本项目的学习路径与实现思路受到 [claude-code-from-scratch](https://github.com/Windy3f3f3f3f/claude-code-from-scratch) 启发。

在跟随原教程实践的过程中，我重新组织了讲解顺序，并记录自己的实现、验证过程与理解。本仓库不是原项目的官方版本。

感谢 [@Windy3f3f3f3f](https://github.com/Windy3f3f3f3f) 及原项目贡献者的开源工作。

## License

[MIT](./LICENSE)
