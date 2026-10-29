# code_agent

一个用于**学习 Agent 原理、准备 Agent 开发岗位面试**的编程 Agent。通过亲手实现各层逻辑，理解
“模型 → 工具 → 观察 → 再决策”循环。核心由用户手写，Codex 提供规格、接口、测试和评审。

[PLAN.md](PLAN.md) 是当前进度、设计契约、学习路线和验收标准的权威入口；
已完成阶段的验收记录与调整原因见 [计划历史](docs/plan-history.md)。
学习安排参考 [Datawhale Hello-Agents](https://github.com/datawhalechina/hello-agents)，
章节对应与实验安排在计划中维护，来源核查见 [参考笔记](docs/research/hello-agents-learning.md)。

面试准备见 [Agent 开发面试问答](docs/interview-guide.md)：参考回答、常见追问、源码与测试入口，以及已实现能力和后续设计的表述边界。

模块职责、运行路径和后续接口安排见 [架构设计](docs/design/architecture.md)；执行链重构的接口、收尾流程与源码阅读顺序见 [执行流程](docs/design/run-flow.md)。

流式响应的传输与解析见 [网络读取、SSE 事件与模型分片](docs/research/streaming-sse-boundaries.md)：说明分片边界、文本和工具调用提取、处理方案及可复现的无网络实验。

设计参考见 [Pi Coding Agent 源码研究](docs/research/pi-coding-agent-design.md)：按固定源码版本梳理循环、上下文与记忆、工具与权限、运行模式、多 Agent 和评测，附核心逻辑与本项目问题速查。

## 当前进度

- **M0、M1、M2a 已验收**：模型接入、非流式工具循环、工具注册与校验、历史配对、取消处理及原生结束协议已完成。
- **M2b 已验收**：流式工具循环、终端事件和模型请求超时配置已接通；真实模型完成独立靶子的修复与独立复核，2026-10-17 用户确认验收，摘要见计划历史。
- **M3 执行环境校验已实现**：规范化并绑定执行目录与 Bash 路径，创建失败显示原因；2026-10-18 完整回归 268 项通过。按调整后范围待验收确认，命令权限规则与审批后移到 M4c。
- **M4a 已实现，M4b 重构与真实 TTY 实验完成**：复用历史、失败/耗尽/取消后继续；清理或内部故障结束会话。阶段仍待用户验收确认。
- **M4c 基础交互已复核**：权限判断、批准/拒绝及结果回灌已接线；2026-10-27 真实终端复核通过并获用户确认。命令解析与授权范围细化后置，完整 M4c 尚未验收。
- **M5a 正在实施**：会话轨迹与只读回放的规格和练习见 [M5a 设计](docs/design/m5a-tracing.md)；尚未接入默认 CLI 或完成验收。
- **当前 CLI 支持两种入口**：`-p` 执行一次任务；终端无参数运行进入多轮会话，`/exit` 或 EOF 退出。正文与工具进度实时呈现，结束由 RunResult 和外层异常处理决定。非终端无参数显示用法并返回 1。

默认只向模型提供 `bash`，通过命令读取、搜索、修改文件及运行测试；已有 `read_file` 和测试保留，
专用工具按实际失败案例添加。不限制单轮工具调用数量，保留最大模型请求轮数、Bash 超时和输出预算。
循环正常结束与任务验收成功分开，后者以测试和 diff 等外部证据判断。

当前学习顺序为最小轨迹、检查点恢复、记忆生命周期与知识检索（M5）→ 上下文预算与第二种模式（M6）→
评测（M7）→ 本地多 Agent 委派、MCP 工具接入、A2A 任务通信（M8）。这些能力均按计划独立验收；TUI 暂缓。

## 快速开始

```bash
uv sync
cp .env.example .env   # 填入 DEEPSEEK_API_KEY
uv run codeagent --version
uv run codeagent -p "你好"
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
分片由 llm/streaming.py 组装；历史提交、工具执行与任务结束判断由核心负责。Agent.run 返回 RunResult(completed/failed/max_steps)，取消和内部故障向外传播；完整契约见执行流程设计。

## 目录结构

按能力域分包，跨能力业务编排集中在 `core/`；UI 调用核心入口，能力通过共享契约协作，
不引入循环依赖。同包复用和配置依赖正常保留，具体边界见 [PLAN.md](PLAN.md)。

```
src/code_agent/
├── config.py       # J 配置
├── events.py       # 全系统共享的事件与消息模型
├── core/           # 编排器(唯一把各能力串起来的地方)
├── llm/            # A 模型层(OpenAI SDK，连接 OpenAI 兼容服务)
├── modes/          # E 模式:react / plan
├── tools/          # D 工具系统
├── context/        # B 上下文管理
├── memory/         # C 记忆管理
├── safety/         # G 执行环境校验与权限决定类型
├── approval/       # G 审批策略、协调与终端确认
├── tracing/        # I 轨迹与只读回放练习（M5a 进行中）
└── ui/             # H 命令行交互层
    ├── cli.py      # 当前入口:--version、-p、终端多轮会话
    ├── repl.py     # 多轮输入、退出、中断与继续
    ├── render.py   # 进度事件与运行结果、取消、错误显示
    └── prompts.py  # 预留确认模块；当前审批输入位于 approval/prompt.py
```

## 文档导航

- [实施计划](PLAN.md)：当前阶段、有效契约、验收标准和协作分工。
- [计划历史](docs/plan-history.md)：已发生的调整与验收证据，测试数量对应记录日期。
- [设计文档](docs/design/)：当前架构、运行流程和各阶段练习；阶段草案中的历史方案以计划为准。
- [面试问答](docs/interview-guide.md)：按项目实际状态组织的复盘题和证据入口。
- [研究笔记导航](docs/research/README.md)：按主题定位来源、实验和设计参考，优先阅读当前问题对应的入口。
