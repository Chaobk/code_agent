# code_agent

一个用于**学习 Agent 原理**的编程 Agent。目标不是做成产品,而是把 agent 的每一层都亲手实现一遍,
从而搞清楚"模型 → 工具 → 观察 → 再决策"这条循环究竟是怎么跑起来的。

完整的设计决策与里程碑见 [PLAN.md](PLAN.md)。

## 当前进度

M0 已完成:项目骨架与 DeepSeek Chat Completions API 对接,支持命令行流式输出。

M1 部分完成:已有非流式 Agent 工具循环、参数校验、错误回灌、最大步数限制和带行号的 `read_file`。
默认 Bash 工具已接入异步执行、统一结果和执行环境绑定,支持格式化历史显示。
CLI 尚未接入 Agent;Bash 输出读取预算与启动错误处理尚未完成。

后续按 **Bash 优先** 路线推进:M1 默认只向模型提供 `bash`,通过命令完成文件读取、搜索、修改与测试;
已有 `read_file` 和测试保留,其他专用工具按实际失败案例再添加。

`LLMClient.complete_turn(messages, tools=...)` 返回完整响应对象,
`stream_turn(messages, tools=...)` 逐个返回完整 chunk,保留工具调用、结束原因和服务返回的用量信息。
两个方法通过 `**kwargs` 透传 `tools`、`tool_choice`、`temperature` 等参数;
显式传入的参数覆盖 Settings 默认值,且仅影响本次请求。`stream` 由方法控制,不得额外传入。
当前 CLI 只提取文本显示;M1 将接入非流式 Agent 闭环,M2 再接入流式工具参数组装与事件输出。

下一步完善 Bash 输出读取预算与启动错误处理,随后接入 CLI,
在独立靶子仓库验收“修改代码并让 pytest 从红变绿”。
执行目录不等于沙箱;Bash 路径及靶子仓库需在验收前准备,具体规格见 `PLAN.md` §5。

当前采用纯命令行方案,保留 `-p` 单次模式;M5 计划增加多轮交互会话、退出、中断和文字确认。TUI 暂缓。当前无参数运行仍显示用法并退出。

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
当前 CLI 尚未接入上述 Agent 执行路径。

## 目录结构

按能力域分包,`core/` 只做编排,各能力子包之间互不 import(依赖方向见 `PLAN.md` §4)。

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
    ├── repl.py     # M5 占位:多轮输入、退出、中断
    ├── render.py   # M1:命令与结果输出;M2:流式事件输出
    └── prompts.py  # M5 占位:终端文字确认
```
