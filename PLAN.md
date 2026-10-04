# code_agent 实施计划

> 本文件是设计访谈(grilling)得出的结论汇总,作为后续实施的唯一依据。
> 状态:**已确认,实施中**;当前进度:**M0 已通过全部判据**(尚未推送)。

---

## 1. 项目定位

- **性质**:学习型项目——目标是彻底搞懂编程 Agent 的工作原理,而非追求产品化。
- **验收判据**:给它一句话任务,它能在本地仓库里自己读代码、改代码、跑命令验证,并完成任务。
- **首个验收场景**:一个**新建的最小靶子仓库**(独立目录、独立 git 仓库),
  内含一个 Python 小模块 + 一个**故意失败**的 pytest 用例;
  验收任务 = "加一个小功能,让测试从红变绿"。
  靶子仓库位置:`D:\workPros\AICoding\code_agent_target`(M0 创建)。

## 2. 已确定的技术决策

| # | 决策点 | 结论 |
|---|---|---|
| Q2 | "从零"的边界 | 模型调用交给库,**主循环/工具层/上下文管理/记忆自己实现**;后续可平滑换成框架能力 |
| Q3 | 技术栈 | **Python**(不用 TypeScript) |
| Q4 | 模型供应商 | **DeepSeek**(`deepseek/deepseek-chat` 常规、`deepseek/deepseek-reasoner` 复杂规划),模型层做成薄抽象便于更换 |
| 追加 | 模型接入层 | **litellm**——统一多供应商调用,免去自写各家适配;模型标识带前缀(如 `deepseek/deepseek-chat`),密钥按前缀自动读环境变量 |
| 追加 | 代码组织 | **按能力域物理分包**(见 §4),`core/` 只做编排,不承载具体能力 |
| Q5/Q9 | 交互形态 | **Textual TUI** + 保留 **`-p` 无界面单次模式** |
| Q6 | MVP 工具集 | `read_file` / `write_file` / `edit_file` / `run_shell` / `list_dir` / `glob` / `search`(调用本机 ripgrep);git 不单独做工具,由 `run_shell` 直接跑 |
| Q7 | 接口与工具调用 | 走 **Responses API**(`litellm.aresponses`,不用 chat.completions):`input` 为 item 序列、工具定义扁平(`type=function`)、流式为语义事件;原生 function calling + 流式 |
| Q8 | 权限与护栏 | **cwd 收窄** + **危险命令黑名单/确认**(`-p` 下危险命令直接拒绝) |
| Q11 | 主循环形态 | 单 agent **顺序 ReAct** 起步;架构上支持**多种模式**,后续加 plan 模式 |
| Q12 | 上下文管理 | 起步用"保留 system + 最近 N 轮 + 丢弃最老工具输出";**文件内容由 agent 按需 `read_file` 拉取,永不自动注入** |
| Q13 | TUI 与异步 | 主循环跑在 asyncio task;shell 用 `create_subprocess_exec`、文件 I/O 用 `asyncio.to_thread`;事件队列把 token 流/工具调用/结果推给界面 |
| Q14 | 工程规范 | uv 管理;`src/` 布局;依赖见 §6;密钥走环境变量 `DEEPSEEK_API_KEY` |
| Q15 | 测试策略 | 三层:工具层纯函数单测 + `FakeLLM` 驱动循环测试 + TUI 轻量冒烟;真模型只手动 smoke |
| Q17 | mode 抽象时机 | **M1 就留极薄 `AgentMode` 协议**(只暴露"生成下一轮请求"与"是否结束"),plan 模式后续加实现 |
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
| G | 安全与权限(cwd 收窄、危险命令拦截、确认、审计) | `safety/` |
| H | 交互层(`-p` 模式、TUI、流式渲染、中断、纠偏) | `ui/` |
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
│   │   └── client.py          # litellm 封装:acompletion + stream + tools + 用量统计
│   ├── modes/
│   │   ├── base.py            # AgentMode 协议(薄接缝)
│   │   ├── react.py           # M1 实现
│   │   └── plan.py            # M6 占位
│   ├── tools/
│   │   ├── registry.py        # 工具注册表(可扩展:未来接 MCP)
│   │   ├── spec.py            # pydantic 参数模型 → JSON Schema
│   │   └── builtin/
│   │       ├── files.py       # read_file / write_file / edit_file / list_dir / glob
│   │       └── shell.py       # run_shell
│   ├── context/
│   │   └── history.py         # 消息历史 + 可替换裁剪策略
│   ├── memory/
│   │   ├── working.py         # M4:会话内工作记忆
│   │   └── longterm.py        # M4:跨会话长期记忆 + 召回
│   ├── safety/
│   │   └── policy.py          # M3:路径约束、命令拦截、确认策略
│   ├── tracing/
│   │   └── jsonl.py           # M4:轨迹落盘与回放
│   └── ui/
│       ├── cli.py             # 入口:`--version`、`-p`
│       └── tui.py             # M5:Textual 界面
└── tests/
```

**依赖方向铁律**:

1. 各能力子包之间**不互相 import**,只共享 `events.py` 里的数据模型。
2. 所有跨能力的连接只发生在 `core/agent.py`。
3. `llm/` 不认识工具与模式;`tools/` 不认识模型与界面;`ui/` 只消费事件、不做决策。

这样后面替换任一能力(换模型库、换上下文策略、加 plan 模式、接 MCP)都不需要动别的包。

## 5. 里程碑

每个里程碑完成后暂停,展示判据结果,用户确认后进入下一个。
**最短可用路径 = M0–M3。**

### M0 骨架与连通性 〔J + A 最小〕

- `uv` 初始化项目,`pyproject.toml`,`requires-python = ">=3.11"`
- 按 §4 建立**全部能力子包骨架**(未到里程碑的子包只放占位模块 + docstring 注明归属)
- `config.py`:用 pydantic-settings 读 `DEEPSEEK_API_KEY`、base_url、默认模型、步数上限
- `llm/client.py`:litellm 薄封装,先只做**流式文本输出**
- `ui/cli.py`:`--version` 与 `-p "..."` 的最小版本
- `events.py`:内核事件模型骨架
- 配好 `ruff` 与 `pytest`
- 创建靶子仓库 `D:\workPros\AICoding\code_agent_target`(小模块 + 失败的 pytest 用例)
- **判据**:`uv run code-agent --version` 可运行;`uv run code-agent -p "你好"` 能把 DeepSeek 的回复**流式**打印出来

### M1 ReAct 循环(只读) 〔A + D 基础 + B 基础 + E 薄协议 + H `-p`〕

- `modes/base.py` 的极薄 `AgentMode` 协议 + `modes/react.py` 实现
- `core/agent.py` 编排器:串起模型调用与工具执行(单 agent 顺序循环)
- `context/history.py`:会话状态与消息历史
- `tools/registry.py` + `tools/spec.py`:工具注册与 schema 生成
- 只读工具:`read_file`(带行号/偏移)、`list_dir`、`glob`、`search`(rg)
- 刹车:最大步数、单轮工具调用上限、连续无进展中止
- **判据**:在靶子仓库里问"这个项目是做什么的",它能自主读文件并答对

### M2 写工具 + shell(闭环) 〔D + F + A 错误回灌〕

- `write_file`、`edit_file`(精确串替换)、`run_shell`
- 工具结果截断策略
- 自验证雏形:工具失败 → 作为观察回灌 → 再尝试
- **判据**:能改代码并跑 pytest 把红测变绿

### M3 安全与权限护栏 〔G〕

- `safety/policy.py`:cwd 强制收窄在指定仓库内
- 危险命令黑名单/拦截(`-p` 下直接拒绝;TUI 下弹确认)
- 审计日志留痕
- **判据**:越界路径被拒;`rm -rf` 类命令被拦

### M4 记忆与持久化 〔C + I 轨迹〕

- `memory/working.py`:任务状态、TODO、已改文件清单
- `memory/longterm.py`:项目级记忆文件,自动注入 prompt
- `tracing/jsonl.py`:每轮 JSONL;支持 `--resume` 恢复会话
- 召回:先用"文件 + rg 关键词",不引向量库
- **判据**:关掉进程后 `--resume` 能继续,并记得改过哪些文件

### M5 Textual TUI 〔H + G 确认弹窗〕

- `ui/tui.py`:流式渲染、工具调用可视化、危险命令确认弹窗
- 可中断当前步、可中途纠偏
- **判据**:在 TUI 里完整跑一遍 M2 的任务

### M6 上下文深化 + 模式扩展 〔B + E + I 成本〕

- 裁剪策略抽成**可替换接口**(默认"丢最老工具输出")
- token 预算与成本报表(litellm 自带成本计算可用)
- prompt 模板版本化
- `modes/plan.py`:**plan-execute** 模式,界面展示/确认计划
- **判据**:配置切换 mode 可跑通;plan 模式能先出计划再执行

### M7 评测与打磨 〔I + J〕

- 小型 eval harness(5–10 个任务,`-p` 批量跑,记录成功率)
- 重试/超时打磨、README/文档、打包
- **判据**:一条命令跑完评测集并输出成功率

## 6. 工程约定

- **依赖清单**:
  - 运行时:`litellm`(模型接入)、`pydantic` + `pydantic-settings`(数据与配置)、`typer` + `rich`(CLI/输出)、`textual`(M5 才加)
  - 开发:`pytest`、`pytest-asyncio`、`ruff`
  - 刻意不引入:`openai`(litellm 已内含)、`tenacity`(由 litellm 的 `num_retries` 覆盖重试)
  - litellm 版本下限参照本机 `mini-swe-agent` 的锁定:`>=1.75.5`,并排除被投毒版本 `1.82.7` / `1.82.8`
- litellm 模型标识:`deepseek/deepseek-chat`、`deepseek/deepseek-reasoner`;密钥环境变量名 `DEEPSEEK_API_KEY`
- 依赖方向见 §4 铁律;新增能力时**加子包 + 在 `core/agent.py` 挂接**,而不是往已有包塞代码
- 所有工具函数必须小而纯,便于单测;副作用集中在执行器里
- 密钥不入库:`.env` 与 `.code_agent/` 已在 `.gitignore` 覆盖范围内

## 7. 前置条件(需要用户提供)

- **`DEEPSEEK_API_KEY` 环境变量**:当前本机**未设置**(Codex 自己的配置里另有一份,但本项目应独立使用一份)。
- 靶子仓库落地位置确认为 `D:\workPros\AICoding\code_agent_target`。

## 8. 协作节奏

- **分工**:核心实现由**用户手写**;Codex 负责脚手架(项目配置、依赖、目录骨架)、接口与签名、
  测试用例、文档,以及评审与调整。
- **每步流程**:Codex 给出「规格 + 接口/空壳 + 一个失败的测试」→ 用户写实现 →
  Codex 评审、跑测试、指出问题 → 通过后进入下一个里程碑。
- **不替用户写的(核心)**:agent 主循环与编排、模式实现、工具执行、上下文裁剪、
  记忆读写、安全策略、TUI 交互逻辑。
- **可以替用户写的(非核心)**:pyproject/依赖/ruff/pytest 配置、包骨架与占位、
  README、测试用例,以及机械性的样板代码。
- 每个里程碑完成后暂停并展示判据,用户确认后继续(Q19)。
- 提交粒度:每个里程碑至少一次提交;由于沙箱内 `.git` 为只读,提交/推送需在沙箱外执行。

---

## 附:环境事实(实施时参考)

- 系统:Windows 11 (build 26200)
- 运行时:Node v24.12.0 / npm 11.6.2 / pnpm 12.3.4;Python 3.12.10 / pip 25.0.1 / **uv 0.9.17**;Java 21;rustup 无默认工具链
- 工具:git 2.52.0、gh 2.96.0(已登录 `Chaobk`)、**ripgrep 15.1.0**
- 未安装:go、docker、make、cmake、jq、fd、yarn、bun、deno、poetry
- 外网:可用(可直连 GitHub API)
- 参考物:本地已有 `mini-swe-agent`(Python 极简 SWE agent,litellm + rich + pydantic)与 `codex` 克隆,可作对照
