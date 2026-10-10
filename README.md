# code_agent

一个用于**学习 Agent 原理**的编程 Agent。目标不是做成产品,而是把 agent 的每一层都亲手实现一遍,
从而搞清楚"模型 → 工具 → 观察 → 再决策"这条循环究竟是怎么跑起来的。

完整的设计决策与里程碑见 [PLAN.md](PLAN.md)。

## 当前进度

M0 已完成:项目骨架与 DeepSeek Chat Completions API 对接,支持命令行流式输出。

M1 已验收:已有非流式 Agent 工具循环、参数校验、错误回灌、最大步数限制和带行号的 `read_file`。
默认 Bash 工具已接入异步执行、统一结果和执行环境绑定,支持格式化历史显示。
stdout/stderr 已并发分块读取并共享 20,000 字符预算,超额输出持续排空但不保留。
Bash 启动失败已转换为统一错误结果,相关回归测试已补齐。
CLI 已接入 Agent,单次任务结束后显示工具调用、结果和最终答案,失败时保留已有历史。
临时结束协议已实现:保留 `END_TASK` 作为独立的末行控制标记,由循环检查,不以正文包含该词判断完成。
有工具调用时先回灌结果再继续;原始历史保留标记,CLI 显示去标记的总结。当前 117 项回归测试通过。

后续按 **Bash 优先** 路线推进:M1 默认只向模型提供 `bash`,通过命令完成文件读取、搜索、修改与测试;
已有 `read_file` 和测试保留,其他专用工具按实际失败案例再添加。

`LLMClient.complete_turn(messages, tools=...)` 返回完整响应对象,
`stream_turn(messages, tools=...)` 逐个返回完整 chunk,保留工具调用、结束原因和服务返回的用量信息。
两个方法通过 `**kwargs` 透传 `tools`、`tool_choice`、`temperature` 等参数;
显式传入的参数覆盖 Settings 默认值,且仅影响本次请求。`stream` 由方法控制,不得额外传入。
当前 CLI 使用非流式 Agent 闭环并显示历史;M2 再接入流式工具参数组装与事件输出。

独立靶子已完成“修改代码并让 pytest 从红变绿”验收：实现文件有修改，测试未改，5 项测试通过。
当前进入 M2a，ToolSpec/ToolRegistry、内置工具工厂与 Agent 接线已实现，工具层 16 项测试通过，完整回归 133 项通过。
工作目录与 Bash 路径由应用绑定，工具结果统一为 ToolResult 并序列化回灌；下一步整理历史、事件和 finish_task 协议。
当前不实现单轮工具调用数量上限；保留最大模型请求轮数、Bash 超时和输出预算。
执行目录不等于沙箱;Bash 路径及靶子仓库需在验收前准备,具体规格见 `PLAN.md` §5。

M2a 先整理工具与历史契约并增加 `finish_task` 控制调用，M2b 再实现流式工具循环。
当前采用纯命令行方案，保留 `-p` 单次模式；M4 增加多轮交互、退出、中断和文字确认，
M5 增加轨迹、恢复与项目记忆；模式协议在 M6 实现第二种模式时再抽出。TUI 暂缓。
当前无参数运行仍显示用法并退出。

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

## 目录结构

按能力域分包，跨能力业务编排集中在 `core/`；UI 调用核心入口，能力通过共享契约协作，
不引入循环依赖。同包复用和配置依赖正常保留，具体边界见 `PLAN.md` §4。

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
    ├── repl.py     # M4 占位:多轮输入、退出、中断
    ├── render.py   # M1:命令与结果输出;M2:流式事件输出
    └── prompts.py  # M4 占位:终端文字确认
```
