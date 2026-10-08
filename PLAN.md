# code_agent 实施计划

> 本文件汇总设计决策与后续实施安排;2026-10-08 按用户决定调整为 **Bash 优先**。
> 状态:**已确认,实施中**;当前进度:**M0 已完成,M1 部分完成**。
> 已有非流式工具循环、参数校验、错误回灌、最大步数限制和 `read_file`;CLI 尚未接入 Agent;Bash 已有启动、超时/取消分支与 psutil 清理,返回封装与输出限额尚未完成。

---

## 1. 项目定位

- **性质**:学习型项目——目标是彻底搞懂编程 Agent 的工作原理,而非追求产品化。
- **验收判据**:给它一句话任务,它能在本地仓库里自己读代码、改代码、跑命令验证,并完成任务。
- **首个验收场景**:一个**新建的最小靶子仓库**(独立目录、独立 git 仓库),
  内含一个 Python 小模块 + 一个**故意失败**的 pytest 用例;
  验收任务 = "加一个小功能,让测试从红变绿"。
  原计划靶子仓库位置:`D:\workPros\AICoding\code_agent_target`;当前执行环境未找到该目录,
  M1 验收前需准备独立靶子仓库并确定实际路径。

## 2. 已确定的技术决策

| # | 决策点 | 结论 |
|---|---|---|
| Q2 | "从零"的边界 | 模型调用交给库,**主循环/工具层/上下文管理/记忆自己实现**;后续可平滑换成框架能力 |
| Q3 | 技术栈 | **Python**(不用 TypeScript) |
| Q4 | 模型供应商 | **DeepSeek**(`deepseek/deepseek-chat` 常规、`deepseek/deepseek-reasoner` 复杂规划),模型层做成薄抽象便于更换 |
| 追加 | 模型接入层 | **litellm**——统一多供应商调用,免去自写各家适配;模型标识带前缀(如 `deepseek/deepseek-chat`),密钥按前缀自动读环境变量 |
| 追加 | 代码组织 | **按能力域物理分包**(见 §4),`core/` 只做编排,不承载具体能力 |
| Q5/Q9 | 交互形态 | **纯命令行**:保留 `-p` 单次模式,M5 增加直接运行 `code-agent` 的多轮交互会话;TUI 暂缓,不列入当前里程碑 |
| Q6 | MVP 工具集 | **只向模型提供一个 `bash` 工具**:读取、搜索、修改、测试与 git 操作均通过 Bash 命令完成;已有 `read_file` 实现与测试保留,后续按失败案例决定是否启用专用工具 |
| Q7 | 接口与工具调用 | 走 **Chat Completions API**(`litellm.acompletion`):输入为 `messages`、工具定义嵌套于 `function`;`complete_turn` 返回完整 `ModelResponse`,`stream_turn` 原样产出 `ModelResponseStream`;支持 function calling 与流式 |
| Q8 | 权限与护栏 | M1 固定执行目录,仅在独立靶子仓库手动验收;**cwd 不是沙箱**。M3 增加路径校验、危险命令拒绝与执行环境隔离评估;M5 接入终端文字确认(默认拒绝);`-p` 与非终端输入始终拒绝危险命令 |
| Q11 | 主循环形态 | 单 agent **顺序 ReAct** 起步;架构上支持**多种模式**,后续加 plan 模式 |
| Q12 | 上下文管理 | M1 先沿用顺序消息历史,限制工具输出长度;M2 抽出历史管理,M6 深化裁剪。**文件内容由 agent 按需调用 Bash 获取,永不自动注入** |
| Q13 | 命令行与异步 | 主循环使用 asyncio;Bash 用 `create_subprocess_exec` 启动,异步工具直接 await,同步工具用 `asyncio.to_thread`;M1 先输出命令与完整结果,M2 再消费流式事件 |
| Q14 | 工程规范 | uv 管理;`src/` 布局;依赖见 §6;密钥走环境变量 `DEEPSEEK_API_KEY` |
| Q15 | 测试策略 | 三层:工具层纯函数单测 + `FakeLLM` 驱动循环测试 + CLI 集成测试(输入/输出、退出、中断与确认);真模型只手动 smoke |
| Q17 | mode 抽象时机 | M1 先用现有 ReAct 循环跑通闭环;**M2 补极薄 `AgentMode` 协议**(只暴露"生成下一轮请求"与"是否结束"),M6 再实现 plan 模式 |
| Q19 | 协作节奏 | **每个里程碑完成后停下**,展示判据运行结果,用户确认后再继续 |
| Q21 | 记忆管理深度 | 会话内工作记忆 **+ 跨会话长期记忆**;召回先用"文件 + rg 关键词",**不引向量库** |
| Q22 | 可观测与评测 | 轨迹 JSONL + token/成本统计 + 可回放(M4/M6);eval harness 放 M7 |
| Q23 | MCP / 多 agent | 现在不做,但在工具系统与 mode 层**留出可扩展形状** |

## 3. 能力域编号

| 编号 | 能力域 | 对应子包 |
|---|---|---|
| A | 模型层(provider/流式/工具调用/重试/超时/token 与成本) | `llm/` |
| B | 上下文管理(历史/裁剪/按需注入/system prompt 与模板) | `context/` |
| C | 记忆管理(会话内工作记忆/跨会话长期记忆/召回) | `memory/` |
| D | 工具系统(注册、schema、执行、结果截断、错误回灌) | `tools/` |
| E | 规划与模式(ReAct / plan-execute / 多模式可插拔) | `modes/` |
| F | 执行与验证(shell、跑测试、自验证循环) | `tools/builtin/`(执行端) |
| G | 安全与权限(路径校验、命令拦截、执行环境边界、确认、审计) | `safety/` |
| H | 交互层(`-p` 单次模式、命令行多轮会话、终端输出、中断、文字确认) | `ui/` |
| I | 可观测与评测(轨迹 JSONL、回放、成本报表、eval) | `tracing/` |
| J | 工程与分发(uv 项目、配置、文档、打包) | 项目根 + `config.py` |

## 4. 架构与目录结构

原则:**一个能力域 = 一个子包**;内核只负责编排,不承载能力实现。

```
code_agent/
├── pyproject.toml
├── .python-version
├── PLAN.md
├── src/code_agent/
│   ├── __init__.py            # __version__
│   ├── config.py              # Settings(pydantic-settings):密钥/base_url/模型/上限
│   ├── events.py              # 全系统共用的事件与消息数据模型(唯一的共享契约)
│   ├── core/
│   │   └── agent.py           # 编排器:串起 llm / modes / tools / context / memory / tracing
│   ├── llm/
│   │   └── client.py          # litellm 封装:acompletion + 完整响应/chunk + tools;M6 用量统计
│   ├── modes/
│   │   ├── base.py            # AgentMode 协议(薄接缝)
│   │   ├── react.py           # M2 从现有循环抽出模式实现
│   │   └── plan.py            # M6 占位
│   ├── tools/
│   │   ├── registry.py        # 工具注册表(可扩展:未来接 MCP)
│   │   ├── spec.py            # pydantic 参数模型 → JSON Schema
│   │   └── builtin/
│   │       ├── files.py       # 已有 read_file;其余专用工具按需增加
│   │       └── shell.py       # M1:run_bash;向模型注册为 bash
│   ├── context/
│   │   └── history.py         # M2:消息历史;M6:可替换裁剪策略
│   ├── memory/
│   │   ├── working.py         # M4:会话内工作记忆
│   │   └── longterm.py        # M4:跨会话长期记忆 + 召回
│   ├── safety/
│   │   └── policy.py          # M3:路径约束、命令拦截、确认策略
│   ├── tracing/
│   │   └── jsonl.py           # M4:轨迹落盘与回放
│   └── ui/
│       ├── cli.py             # 参数解析、--version、-p;M5 分发到交互会话
│       ├── repl.py            # M5:终端多轮输入、退出与中断
│       ├── render.py          # M1:命令与结果输出;M2:流式事件输出
│       └── prompts.py         # M5:终端文字确认(默认拒绝)
└── tests/
    └── code_agent/
        ├── test_config.py
        ├── core/
        │   └── test_agent.py
        ├── llm/
        │   └── test_client.py
        ├── tools/
        │   └── builtin/
        │       ├── test_files.py
        │       └── test_shell.py  # M1 新增
        └── ui/
            └── test_cli.py
```

**依赖方向铁律**:

1. 各能力子包之间**不互相 import**,只共享 `events.py` 里的数据模型。
2. 所有跨能力的连接只发生在 `core/agent.py`。
3. `llm/` 不认识工具与模式;`tools/` 不认识模型与交互层;`ui/` 负责终端输入、输出与确认,不做 agent 决策。
4. `ui/repl.py` 只维护终端交互流程;消息历史由 `context/` 管理,权限判断由 `safety/` 管理,能力连接仍由 `core/agent.py` 完成。

这样后面替换任一能力(换模型库、换上下文策略、加 plan 模式、接 MCP)都不需要动别的包。

## 5. 里程碑

每个里程碑完成后暂停,展示判据结果,用户确认后进入下一个。
**首个任务闭环 = M0–M1;安全与权限基础 = M3。**
M1 的 Bash 可以写文件和运行程序,不再定义为只读阶段。

### M0 骨架与连通性 〔J + A 最小〕

- `uv` 初始化项目,`pyproject.toml`,`requires-python = ">=3.11"`
- 按 §4 建立**全部能力子包骨架**(未到里程碑的子包只放占位模块 + docstring 注明归属)
- `config.py`:用 pydantic-settings 读 `DEEPSEEK_API_KEY`、base_url、默认模型、步数上限
- `llm/client.py`:litellm 薄封装,通过 `**kwargs` 传递可选 `tools` 等请求参数,保留完整响应和流式 chunk;M0 调用方只展示文本
- `ui/cli.py`:`--version` 与 `-p "..."` 的最小版本
- `events.py`:内核事件模型骨架
- 配好 `ruff` 与 `pytest`
- 创建靶子仓库 `D:\workPros\AICoding\code_agent_target`(小模块 + 失败的 pytest 用例)
- **判据**:`uv run code-agent --version` 可运行;`uv run code-agent -p "你好"` 能把 DeepSeek 的回复**流式**打印出来

### M1 Bash 工具与最小任务闭环 〔A + D + F + H `-p`〕

- **当前已实现**:非流式 ReAct 循环、工具 schema 生成、参数校验、结果与错误回灌、最大步数限制;`read_file` 已有实现和基础测试
- **下一步**:补全 `tools/builtin/shell.py` 的结果返回、读取预算与错误处理;完成后接入 Agent 并让默认注册表只向模型暴露 `bash`
- Bash 的文件读取、目录发现、搜索、修改、测试与 git 命令由模型生成;`list_dir`、`glob`、`search`、`write_file`、`edit_file` 暂不逐一实现
- `core/agent.py`:支持直接 await 异步工具并保留同步工具的 `to_thread` 路径;继续使用 `complete_turn`,以 `role=tool` 和匹配的 `tool_call_id` 回灌结果
- CLI 通过核心编排入口执行任务,传入 Settings 的步数上限和执行目录;完成时给出最终答案,移除提示词中的 `END_TASK` 回复要求
- `ui/render.py`:显示工具命令、结果和最终答案;M1 Agent 路径先采用非流式输出,不等待流式工具参数组装完成才验收
- 刹车:最大步数、单轮工具调用上限、Bash 超时、输出截断;命令非零退出作为观察回灌,允许模型修正后重试
- 测试顺序:Bash 执行器单测 → FakeLLM 异步工具调用与错误回灌测试 → CLI 入口测试 → 真模型手动验收
- **判据**:在独立靶子仓库通过 `-p` 执行任务,模型只使用 `bash` 读代码、修改并跑 pytest,把红测变绿;展示命令轨迹、退出码和最终 diff

#### M1 下一步规格:Bash 工具

> 2026-10-09 开发快照:53 个测试通过,2 个正常执行测试因 run_bash 缺少 ToolResult 返回而失败;8 个清理测试通过。M1 尚未完成。

- 模型工具名:`bash`;参数模型:`BashArgs`;执行函数:`async def run_bash(...) -> ToolResult`
- 统一结果契约放在 `events.py`:仅包含 `output: JsonValue`、`status: ToolStatus`、`truncated: bool`;状态为 `completed`、`error`、`timed_out`,不使用 `is_error` 或 `metadata`;失败原因直接放入 `output`
- 模型参数:`command: str`(拒绝空白命令)、`timeout_seconds: int = 30`(范围 1–120 秒)
- 执行目录与 Bash 可执行文件路径由配置及核心绑定,不作为模型可自由指定的工具参数;计划配置名为 `CODE_AGENT_WORKSPACE_DIR` 与 `CODE_AGENT_BASH_PATH`
- 使用 `create_subprocess_exec(bash_path, "--noprofile", "--norc", "-c", command, cwd=workspace_dir, ...)`;主机执行器不再通过其他 shell 拼接命令
- 每次调用启动独立进程;`cd`、`export` 不跨调用保留。工具描述说明 shell 类型、执行目录与当前可用命令
- Windows 使用原生 Git Bash 等已安装的 Bash;Bash 不可用时明确报错,不静默切换到 PowerShell、cmd 或 WSL
- `ToolResult.output` 保留结构化 JSON,包含 `stdout`、`stderr`、原始 `exit_code`;正常退出(含非零)状态为 `completed`;超时为 `timed_out`,退出码为 `null`;无法启动等执行错误为 `error`,输出中说明失败原因;截断标记放在 `ToolResult.truncated`
- Agent 后续接入时把统一结果序列化为模型消息文本,携带结果、状态和截断信息;ToolResultEvent 与现有 read_file 的字符串路径在该步骤一起迁移
- 进程树清理采用 psutil 7.x,启动时保留进程身份,通过 asyncio.to_thread 执行同步清理;这是已发现后代的快照清理,父进程提前退出和新生后代的严格生命周期保证仍需启动期进程组或 Job Object
- 固定输出预算,stdout/stderr 合计最多 20,000 字符,截断时标记 `truncated`;读取阶段也限制缓冲增长并持续排空管道,避免只在收集完全部输出后截断
- 超时或任务取消时清理执行进程及其启动的子进程、等待回收;取消继续传播,不作为普通工具错误吞掉
- 首个测试:在临时目录创建文件,执行 `cat example.txt`,断言读取内容、退出码和执行目录正确;用假 subprocess 测试固定 Bash 参数和 cwd,有 Bash 的环境补真实集成测试
- 后续测试覆盖 stderr、非零退出、超时、取消、输出截断和 Bash 缺失;测试不访问真实模型

### M2 流式输出与模块边界整理 〔A + B + D + E + H〕

- `modes/base.py` 的极薄 `AgentMode` 协议 + `modes/react.py` 实现
- `context/history.py`:会话状态与消息历史;保留 assistant 工具调用与对应结果的配对关系
- `tools/registry.py` + `tools/spec.py`:从 Agent 抽出工具注册、schema、参数校验与执行职责;默认工具集继续只含 `bash`
- `core/agent.py`:接入 `stream_turn`,按工具调用 index 组装名称与参数,结束并校验后才执行;以 `role=tool` 和匹配的 `tool_call_id` 回灌
- `ui/render.py`:消费 events 定义的流式文本、工具调用、结果和错误;补连续无进展中止
- 专用工具作为按需扩展项,有具体失败案例和对比结果后再启用,不作为 M2 验收前置条件
- **判据**:同一靶子任务在流式路径完成;分片工具参数只执行一次,异常结束不执行未完成命令,FakeLLM 和 CLI 回归测试通过

### M3 安全与权限护栏 〔G〕

- `safety/policy.py`:校验执行目录与专用文件工具路径,增加 Bash 命令执行前的策略检查
- 明确任意 Bash 可通过绝对路径、脚本和子进程访问 cwd 之外;字符串黑名单只作为拦截措施,不视为完整隔离。需要强路径边界时采用并验证 OS 沙箱或受限容器,记录实际保护范围
- 危险命令黑名单/拦截:M3 阶段统一拒绝;M5 在交互会话接入文字确认,`-p` 与非终端输入始终拒绝
- 确认由交互层采集,安全策略仍在 `safety/` 中判断;空输入、EOF 或中断均不得授权执行
- 审计日志留痕
- **判据**:目录与文件路径越界被拒;`rm -rf` 类命令被拦;通过实际 Bash 命令验证所选隔离环境的文件边界,或明确标注本地模式仍需信任命令

### M4 记忆与持久化 〔C + I 轨迹〕

- `memory/working.py`:任务状态、TODO、已改文件清单
- `memory/longterm.py`:项目级记忆文件,自动注入 prompt
- `tracing/jsonl.py`:每轮 JSONL;支持 `--resume` 恢复会话
- 召回:先用"文件 + rg 关键词",不引向量库
- **判据**:关掉进程后 `--resume` 能继续,并记得改过哪些文件

### M5 命令行多轮会话 〔H + G 文字确认〕

- `ui/cli.py`:无 `-p` 时进入交互会话;`-p` 继续执行单次任务并退出
- `ui/repl.py`:逐轮读取任务,复用核心会话;支持 `/exit` 与 EOF 退出
- `ui/render.py`:在终端输出流式文本、工具调用、结果和错误,不维护全屏布局
- `ui/prompts.py`:危险操作文字确认,默认拒绝;`-p` 或非终端输入不得等待确认
- Ctrl+C 取消当前任务并回到输入提示;用户可在下一轮补充或纠正要求
- 增加 CLI 集成测试,覆盖多轮输入、退出、中断、确认与非交互拒绝
- **判据**:在同一命令行会话完成 M1 的改代码与测试任务并继续追问;拒绝确认时不执行危险操作;`-p` 路径仍可自动化运行

TUI 暂缓:当前不保留专用模块或引入 Textual;以后需要时单独制定里程碑。

### M6 上下文深化 + 模式扩展 〔B + E + I 成本〕

- 裁剪策略抽成**可替换接口**(默认"丢最老工具输出")
- token 预算与成本报表(litellm 自带成本计算可用)
- prompt 模板版本化
- `modes/plan.py`:**plan-execute** 模式,在终端展示计划;交互会话文字确认后执行,`-p` 或非终端输入遇到需要确认的计划时拒绝执行并退出
- **判据**:配置切换 mode 可跑通;plan 模式能先出计划再执行

### M7 评测与打磨 〔I + J〕

- 小型 eval harness(5–10 个任务,`-p` 批量跑,记录成功率)
- 重试/超时打磨、README/文档、打包
- **判据**:一条命令跑完评测集并输出成功率

## 6. 工程约定

- **依赖清单**:
  - 运行时:`litellm`(模型接入)、`pydantic` + `pydantic-settings`(数据与配置)、`typer` + `rich`(命令行参数与终端输出);当前命令行方案不引入 Textual
  - 开发:`pytest`、`pytest-asyncio`、`ruff`
  - 刻意不引入:`openai`(litellm 已内含)、`tenacity`(由 litellm 的 `num_retries` 覆盖重试)
  - litellm 版本下限参照本机 `mini-swe-agent` 的锁定:`>=1.75.5`,并排除被投毒版本 `1.82.7` / `1.82.8`
- litellm 模型标识:`deepseek/deepseek-chat`、`deepseek/deepseek-reasoner`;密钥环境变量名 `DEEPSEEK_API_KEY`
- 依赖方向见 §4 铁律;新增能力时**加子包 + 在 `core/agent.py` 挂接**,而不是往已有包塞代码
- 工具参数校验与结果格式化保持小而纯;Bash 等副作用集中在执行器里,通过临时目录或假进程测试
- 密钥不入库:`.env` 已忽略;M4 落盘轨迹前补充 `.code_agent/` 忽略规则,禁止将凭据写入命令日志

## 7. 前置条件(需要用户提供)

- **`DEEPSEEK_API_KEY`**:真模型验收前配置项目独立凭据;单测使用 FakeLLM,不依赖真实密钥
- **Bash**:当前执行环境的 PATH 以及常见 Git Bash 安装位置均未找到可执行文件;实现前确认安装位置并配置 `CODE_AGENT_BASH_PATH`,实际执行版本不得静默切换 shell
- **Python 与 uv**:当前执行环境找不到 `uv`,仓库没有 `.venv`;需要恢复开发环境后运行测试,不沿用历史“已通过”作为本轮结果
- **独立靶子仓库**:原路径 `D:\workPros\AICoding\code_agent_target` 当前不存在;验收前准备最小模块与故意失败的 pytest 用例,并设置工作目录

## 8. 协作节奏

- **分工**:核心实现由**用户手写**;Codex 负责脚手架(项目配置、依赖、目录骨架)、接口与签名、
  测试用例、文档,以及评审与调整。
- **每步流程**:Codex 给出「规格 + 接口/空壳 + 一个失败的测试」→ 用户写实现 →
  Codex 评审、跑测试、指出问题 → 通过后进入下一个里程碑。
- **不替用户写的(核心)**:agent 主循环与编排、模式实现、工具执行、上下文裁剪、
  记忆读写、安全策略、命令行会话与确认逻辑。
- **可以替用户写的(非核心)**:pyproject/依赖/ruff/pytest 配置、包骨架与占位、
  README、测试用例,以及机械性的样板代码。
- 每个里程碑完成后暂停并展示判据,用户确认后继续(Q19)。
- 提交粒度:每个里程碑至少一次提交;由于沙箱内 `.git` 为只读,提交/推送需在沙箱外执行。

---

## 附:环境事实(实施时参考)

以下为早期记录;当前环境可用性以 §7 的检查结果为准。

- 系统:Windows 11 (build 26200)
- 运行时:Node v24.12.0 / npm 11.6.2 / pnpm 12.3.4;Python 3.12.10 / pip 25.0.1 / **uv 0.9.17**;Java 21;rustup 无默认工具链
- 工具:git 2.52.0、gh 2.96.0(已登录 `Chaobk`)、**ripgrep 15.1.0**
- 未安装:go、docker、make、cmake、jq、fd、yarn、bun、deno、poetry
- 外网:可用(可直连 GitHub API)
- 参考物:本地已有 `mini-swe-agent`(Python 极简 SWE agent,litellm + rich + pydantic)与 `codex` 克隆,可作对照
