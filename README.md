# code_agent

一个用于**学习 Agent 原理、准备 Agent 开发岗位面试**的编程 Agent。通过亲手实现各层逻辑，理解
“模型 → 工具 → 观察 → 再决策”循环。核心由用户手写，Codex 提供规格、接口、测试和评审。

[PLAN.md](PLAN.md) 是当前进度、设计契约、学习路线和验收标准的权威入口；
已完成阶段的验收记录与调整原因见 [计划历史](docs/plan-history.md)。
学习安排参考 [Datawhale Hello-Agents](https://github.com/datawhalechina/hello-agents)，
章节对应与实验安排在计划中维护，来源核查见 [参考笔记](docs/research/hello-agents-learning.md)。

面试准备见 [Agent 开发面试问答](docs/interview-guide.md)：参考回答、常见追问、源码与测试入口，以及已实现能力和后续设计的表述边界。

模块职责、运行路径和后续接口安排见 [架构设计](docs/design/architecture.md)。

流式响应的传输与解析见 [网络读取、SSE 事件与模型分片](docs/research/streaming-sse-boundaries.md)：说明分片边界、文本和工具调用提取、处理方案及可复现的无网络实验。

## 当前进度

- **M0、M1、M2a 已验收**：模型接入、非流式工具循环、工具注册与校验、历史配对、取消处理及原生结束协议已完成。
- **M2b 已验收**：流式工具循环、终端事件和模型请求超时配置已接通；真实模型完成独立靶子的修复与独立复核，2026-10-17 用户确认验收，摘要见计划历史。
- **M3 执行环境校验已实现**：规范化并绑定执行目录与 Bash 路径，创建失败显示原因；2026-10-18 完整回归 268 项通过。按调整后范围待验收确认，命令权限规则与审批后移到 M4c。
- **下一步准备 M4a**：先做终端多轮输入与历史复用，再做 M4b 失败/取消后继续，最后接 M4c 权限与确认。
- **当前 CLI 为流式单次模式**：使用 `-p` 执行任务，实时呈现正文、工具调用、结果和错误；无参数运行显示用法并退出。工具调用在整轮流正常结束且结构校验通过后执行。

默认只向模型提供 `bash`，通过命令读取、搜索、修改文件及运行测试；已有 `read_file` 和测试保留，
专用工具按实际失败案例添加。不限制单轮工具调用数量，保留最大模型请求轮数、Bash 超时和输出预算。
循环正常结束与任务验收成功分开，后者以测试和 diff 等外部证据判断。

后续主线包括终端多轮会话与操作确认（M4a–c）、轨迹与恢复及记忆生命周期、知识检索（M5）、上下文与第二种模式（M6）、
评测（M7），以及本地多 Agent 委派、MCP 工具接入、A2A 任务通信（M8）。这些能力均按计划独立验收；TUI 暂缓。

## 快速开始

```bash
uv sync
cp .env.example .env   # 填入 DEEPSEEK_API_KEY
uv run code-agent --version
uv run code-agent -p "你好"
```

Agent 示例入口从 Settings 读取执行环境,可在 `.env` 中设置:

```dotenv
CODE_AGENT_WORKSPACE_DIR=C:/path/to/your/project
CODE_AGENT_BASH_PATH=C:/Program Files/Git/bin/bash.exe
```

未设置时,工作目录默认使用启动程序时的当前目录,Bash 默认通过 PATH 查找 `bash`。
工作目录由核心规范化为绝对路径,同时用于系统提示词和 Bash 执行器。
模型只指定 `command` 与 `timeout_seconds`,不指定执行目录或 Bash 路径。
当前 CLI 通过 `create_agent(settings)` 使用上述 Agent 执行路径。
执行目录不等于沙箱；真实改代码验收使用独立靶子，准备要求见 [PLAN.md](PLAN.md)。

## 模型接口

`LLMClient.complete_turn(messages, tools=...)` 返回完整响应对象，
`stream_turn(messages, tools=...)` 逐个返回完整 chunk，保留工具调用、结束原因和服务返回的用量信息。
两个方法通过 `**kwargs` 透传 `tools`、`tool_choice`、`temperature` 等参数；
显式传入的参数覆盖 Settings 默认值，且仅影响本次请求。`stream` 由方法控制，不得额外传入。
分片组装、工具执行与任务结束判断由核心负责。

## 目录结构

按能力域分包，跨能力业务编排集中在 `core/`；UI 调用核心入口，能力通过共享契约协作，
不引入循环依赖。同包复用和配置依赖正常保留，具体边界见 [PLAN.md](PLAN.md)。

```
src/code_agent/
├── config.py       # J 配置
├── events.py       # 全系统共享的事件与消息模型
├── core/           # 编排器(唯一把各能力串起来的地方)
├── llm/            # A 模型层(litellm)
├── modes/          # E 模式:react / plan
├── tools/          # D 工具系统
├── context/        # B 上下文管理
├── memory/         # C 记忆管理
├── safety/         # G 安全与权限
├── tracing/        # I 轨迹与评测
└── ui/             # H 命令行交互层
    ├── cli.py      # 当前入口:--version、-p
    ├── repl.py     # M4a/b 占位:多轮输入、退出、中断
    ├── render.py   # 当前正文增量、工具调用与结果、错误及停止事件输出
    └── prompts.py  # M4c 占位:终端文字确认
```
