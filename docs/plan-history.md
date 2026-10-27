# 计划历史与验收记录

本文件保存从原计划迁出的历史验收、阶段规格和调整依据，供追溯时阅读。当前进度、有效契约和后续安排以 [PLAN.md](../PLAN.md) 为准；历史测试结果不表示当前工作区已经通过回归。M1 的 END_TASK 协议等旧规则仅用于复盘，已由后续决策替换。

## 2026-10-27 M4c 基础交互真实终端复核

- 用户确认接受基础交互小步结果，并开始 M5a；这不代表完整 M4c 或其他待验收阶段已获确认。
- 使用 OpenAI ChatCompletionChunk 脚本模型，不访问远程模型；真实 CLI 交互入口、审批输入、Agent/Registry 与本机 Git Bash 保持原实现。单个 Agent 完成 approve、reject、enter、space、eof、sigint、cancel、after 八次运行，stdin.isatty() 为 true。
- 批准 o 后 Bash 执行一次；r、Enter、纯空白、实际 Ctrl+Z/Enter（Windows EOF）及审批时 Ctrl+C 均生成 permission_denied/not_started 观察，handler 未执行，模型接收观察后正常完成。八个调用各有一个对应结果。
- cancel 案例批准后，确认 Bash 已写出启动标记，再从 TTY 发送 Ctrl+C；当前运行传播 CancelledError，观察结算为 unknown，所有运行结束时 pending 为空。命令尾部文件未生成；三个 Bash 进程均有退出码，stdout/stderr 均 EOF。随后在普通输入时 Ctrl+C 仍只丢弃当前行，after 任务再次批准并执行成功。
- `/exit` 后应用捕获退出码 0、原 SIGINT 处理器恢复、客户端关闭。PTY PowerShell 宿主返回 1，与应用退出码分别记录；无输出收尾超时或 transport 诊断。独立验证脚本共 23 项检查通过。
- 首次沙箱内尝试启动 Bash 返回 WinError 5，单独保留该次失败；自动审批允许后在沙箱外完成上述实验。全部副作用局限于独立 artifacts 工作目录。原始脚本为 `artifacts/m4c-terminal-20261027/terminal_smoke.py`，成功记录在 `verified/console.txt`、`verified/report.json`、`verified/verification.json`，独立核查脚本为同级 `verify.py`。
- 复核时未修改应用源码或正式测试，未重新执行完整 pytest；382 项通过仍属于 2026-10-26 的历史回归结果。复核完成后用户确认基础交互小步并进入 M5a；完整 M4c 未验收，原有 a.txt 保留。

## 2026-10-26 学习顺序调整：权限基础收尾后推进记忆与上下文

- 用户确认优先贯通 Agent 的其他主线，授权调整计划。当前先完成 M4c 基础交互的真实终端复核：审批批准、r/空白/EOF/Ctrl+C 拒绝、执行中取消及会话续用；展示证据并获得用户确认后进入 M5a。完整 M4c 保持未验收，M3/M4a/M4b 的既有验收状态不因本次调整改变。
- Bash 解析、Git 等命令副作用规则、会话授权范围细化和通用职责整理保留为后置完善项，不再作为进入 M5 的前置条件。后续实验若暴露影响执行正确性、取消收尾或历史配对的故障，先修复具体故障；其余改进按案例安排，主线贯通后集中复核待办。
- 后续顺序明确为 M5a 最小 JSONL 轨迹与只读回放 → M5b 已配对检查点恢复 → M5c 记忆生命周期 → M5d 知识检索与注入 → M6a 上下文预算，再继续模式、评测和多 Agent/协议通信。已有 History 仍负责完整会话记录与配对，长期记忆和本次模型输入选择分别实施；本次不重新编号或删减 M5–M8。
- 本次只修改计划与进度说明，未实施轨迹、恢复、记忆或上下文裁剪，未运行新的真实终端验收；既有 382 项测试结果仍仅属于审批输入提交的验证证据。计划调整不自动授权提交、推送或进入下一实施步。

## 2026-10-26 审批空输入与 Ctrl+C 拒绝

- 用户选择审批时 Ctrl+C 与 EOF 都拒绝当前操作，拒绝观察回灌后模型可继续；其他运行阶段仍取消当前运行。用户实现空白输入直接返回 r，以及审批 input 期间临时安装 signal.default_int_handler、在 finally 恢复原 SIGINT 处理器。避免审批读取触发 REPL 的 Task.cancel；不改 REPL 或核心执行链。
- Codex 补上 `[r/Enter] 拒绝` 提示、说明与回归测试，覆盖空白只读一次、合法选择归一化、非法输入重试及上限、EOF/KeyboardInterrupt、非交互入口、正常和异常退出时恢复原处理器。CLI 集成测试调用当前注册的处理器，验证审批拒绝、工具执行零次、拒绝结果回灌后模型继续、恢复普通输入及会话退出时恢复原处理器；假模型在请求前让出事件循环，防止遗漏待处理任务取消。
- 重点回归 49 项通过。完整命令：`.venv/Scripts/python.exe -B -m pytest -q --tb=short -p no:cacheprovider --basetemp=artifacts/approval-sigint-20261026-full-01`，**382 项通过**。因既有 Windows 沙箱临时目录访问问题在沙箱外运行，未访问真实模型服务。`ruff check . --no-cache`、`ruff format --check src tests --no-cache`、`git diff --check` 通过。
- 自动化直接调用信号处理器，不代表真实终端按键实验；真实 Ctrl+C/EOF 与工具执行中取消仍待手动复核，不宣称完整 M4c 已验收，也不将原 LiteLLM 协程警告归因于本次修复。参考项目的输入语义与本项目选择见[研究笔记](research/approval-empty-input-20261026.md)。原有未跟踪文件 a.txt 不纳入提交。

## 2026-10-26 启动性能排查与 OpenAI SDK 迁移

- 用户报告启动约五六秒延迟。本机直接执行 `.venv/Scripts/codeagent.exe --version` 两次分别为 9.025 秒、7.953 秒；空 Python 进程约 0.08 秒。导入计时定位到 CLI → core.agent → LiteLLM，单次 LiteLLM 累计导入约 6.398 秒；其价格表远程请求在当前环境连接失败后回退本地。强制使用本地价格表后仍约 5.3–5.5 秒。
- 用户明确授权 Codex 直接迁移为 OpenAI SDK。保留 `LLMClient.complete_turn/stream_turn`、原始流分片、组装器算法与核心历史结算；默认仍使用 DeepSeek 服务及 `DEEPSEEK_API_KEY`，模型配置改为 `deepseek-chat`，请求时兼容旧 `deepseek/` 前缀，未修改本地 `.env`。连接池在首次请求创建，跨轮复用；请求级地址、密钥、模型与超时覆盖不改变默认值。显式设置 `max_retries=0`。
- 新增内存 HTTP 传输测试，验证真实 SDK 的请求序列化、覆盖隔离、无自动重试，以及正常结束、提前关闭、取消、超时和断连时关闭响应。实测 SDK 在 SSE 读取时直接抛出 HTTPX 异常，模型层将其转换为 `APITimeoutError`／`APIConnectionError`，核心继续在模型边界返回 `RunResult(failed)`。CLI 在退出单次任务或整个会话时调用 `Agent.close()`；单次交互运行取消仍可继续会话。
- 全量验证：`uv run --offline --locked pytest -q -p no:cacheprovider --basetemp=artifacts/openai-sdk-migration/pytest-final-01 --tb=short`，**366 项通过**。测试因 Windows 沙箱临时目录访问限制在沙箱外运行，模型请求被模拟；没有调用真实模型服务。`ruff check . --no-cache`、`ruff format src tests --check --no-cache`、`git diff --check`、离线构建 sdist/wheel 和 `uv pip check` 通过。全仓格式检查仍有既有 3 份研究笔记的代码块格式差异，未修改无关内容。
- 锁文件从原有依赖集合移除 LiteLLM 及其额外依赖，虚拟环境已离线同步，OpenAI SDK 为 2.54.0。最终直接执行 `--version` 三次为 1.414、1.309、1.266 秒；`uv run --offline --locked codeagent --version` 为 1.298、1.341、1.318 秒。此为版本查询的进程耗时，不包含首次 HTTP 客户端初始化或模型响应。原始计时与构建产物放在已忽略的 `artifacts/openai-sdk-migration/`。
- 本次为用户授权的维护例外，不改变后续默认协作分工，不推进或确认 M4b/M4c 里程碑验收；提交仅包含 SDK 迁移相关修改，原有未跟踪文件 `a.txt` 保持原样。

## 2026-10-23 权限基础流程接线与自动化回归

- 用户手写 Gate、Agent 和 Registry 接线：按会话保存策略与授权缓存；校验后的工具参数经过异步执行前接点，拒绝时 handler 不执行，ToolResult 按调用 ID 写入历史并呈现。Bash 删除旧的内部审批，保留简单规则和策略；命令解析及精细 scope_key 后续完善。
- CLI 仅在交互入口注入真实确认函数，-p 不提供确认能力；测试用的自动批准 lambda 已从应用入口移除。确认函数未配置时 ASK 明确拒绝，已有硬性 DENY 先于会话授权缓存。当前同步确认尚未与 REPL 的 Task 取消整合，不据此声称确认中 Ctrl+C 可正常续用。
- Codex 适配原有 Agent/Registry/CLI 测试，并新增 32 项 Gate、执行前接点、FakeLLM 循环与 CLI 权限回归。原循环测试通过测试辅助函数显式注入 ALLOW，只覆盖已注册工具；该辅助逻辑不进入应用路径，权限专项测试仍使用真实 Gate 与受控策略。
- 覆盖 allow、deny、ask 批准/拒绝、确认不可用、会话授权与 reset、硬拒绝覆盖旧批准；非法调用不进入审批，参数副本修改不影响 handler，审批失败/取消不执行工具；拒绝结果配对回灌，执行前中断保留已有观察、结清其余 not_started，随后可继续运行。
- 重点回归 57 项通过。完整命令：`.venv/Scripts/python.exe -B -m pytest -q --tb=short --maxfail=5 -p no:cacheprovider --basetemp=artifacts/permissions-20261023-full-v1`，**352 项通过**，1 条既有 Pydantic ReadOnly 依赖警告；进程设置 `LITELLM_LOCAL_MODEL_COST_MAP=True`，未访问真实模型服务。因既有 Windows 沙箱临时目录权限问题，测试在沙箱外运行，临时材料仅放项目 artifacts。
- `ruff check . --no-cache`、`ruff format --check src tests` 和 `git diff HEAD --check` 通过。全仓格式检查另有 3 份既存研究笔记的 Python 代码块格式差异，未修改这些无关内容。自动化确认使用脚本输入，审批等待中断使用注入的异步接点，不代替真实 TTY Ctrl+C 或真实模型验收。
- 本次保存的是权限基础流程。基础小步待用户验收，完整 M4c 未完成，M4b 的待验收状态保持；Bash 解析、授权范围细化与真实确认取消整合不在本次提交中自动补齐。工作区未跟踪的空文件 a.txt 不纳入提交。

## 2026-10-23 权限方案调整：先打通简单流程，再逐步完善

- 用户明确当前先写简单版本，先具备权限判断、确认、执行或拒绝及结果回灌，再完善设计与 Bash 解析；Codex 继续负责指导、规格、测试与评审，核心由用户手写。
- PLAN.md 将 M4c 拆为当前基础流程及后续完善安排。当前沿用 Policy / Gate 和简单规则，优先修复阻止运行的导入、枚举比较、结果构造和确认接线；不要求先完成解析器、独立模型审查或通用架构重构。
- 后续明确记录 Bash 解析及副作用规则、会话授权范围、统一执行前入口、真实确认与取消整合；已有两 token scope_key 和命令名放行仅视为临时流程实现，不作为可靠授权或隔离的证据。
- 原权限设计草案保留为后续参考，并在文首标明当前范围，避免其中完整方案继续成为基础版的隐含前置条件。独立模型审查保留候选，不预先确定必须采用。
- 本次仅修改计划文档，未修改运行代码、依赖或测试；不宣称基础流程已通过，也不改变 M4b 的待验收状态或宣称完整 M4c 已验收。当前小步完成后展示证据，由用户确认后续顺序；M5–M8 学习主线保留。

## 2026-10-21 Git Bash 取消故障、进程组修复与上游对照

- 用户真实输入 `sleep 20 && echo "done"`，取消后报输出收尾超时和事件循环关闭时的
  transport 析构异常。直接取消 Task 复现同一超时：Windows 原生快照漏掉 MSYS sleep，
  Bash 退出后 sleep 仍持有输出管道。此前 Python 子进程实验未覆盖该场景。
- 用户先要求规格与测试，随后明确授权 Codex 完成修复，并要求参考成熟实现、缩减代码。
  对照 mini-swe-agent、Codex、OpenCode 官方源码后，实测 taskkill /T 仍漏 sleep；
  最终删除 MSYS 解析方案，改为 Windows 启动时建立独立控制台进程组，收尾定向发送
  CTRL_BREAK，等待 100ms，再保留原生回收与 2 秒输出收尾。控制事件失败也尝试回收。
- `.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider
  --basetemp=artifacts/bash-cancel-20261021/pytest-complete --tb=short`：**320 项通过**，
  1 条既有 Pydantic ReadOnly 依赖警告。新增真实 Git Bash 取消、超时，以及信号失败
  仍回收的测试；移除废弃 MSYS 解析接口的三项测试，未撤销真实故障回归。
- 真实 TTY、脚本模型和 Git Bash：sleep → 实际 Ctrl+C → 回到提示符 → after 再调用
  Bash → /exit。应用捕获退出码 0，历史 pending 为空，两个进程的 stdout/stderr 均 EOF，
  记录的原生进程已退出，输出无超时或 transport 析构异常。PTY PowerShell 宿主返回 1，
  与应用退出码分别记录。未访问真实模型服务。
- 原始材料在 `artifacts/bash-cancel-20261021/`，TTY 记录在 `tty-fixed/`；
  [修复说明](design/m4b-git-bash-cancellation.md)和[上游对比](research/subprocess-cleanup-comparison.md)
  记录适用边界。该机制要求同一控制台，不替代 Job Object 或 OS 沙箱，不代表 M4b 已获用户验收。

## 2026-10-20 执行链重构、完整回归与真实 TTY 实验

- 用户确认“直接循环、明确结果、集中收尾”的方案，随后明确授权本次由 Codex 完成全部重构，供用户阅读代码；后续默认手写分工保留。
- `Agent.run` 直接返回不可变 `RunResult(completed/failed/max_steps)`；核心只发送正文、工具调用、工具结果三类进度事件，删除旧终态事件和未使用的事件占位。`ModelProtocolError` 与已知 LiteLLM 异常在模型边界转成受控失败，显示故障与内部错误继续传播。
- 核心用唯一 `finally` 结清当前批次，保留确定观察、补齐 unknown/not_started；收尾只写历史，不重试显示回调。Registry 移除 handler 宽捕获；文件操作的预期失败由文件工具返回结果。
- Bash 将身份查询、输出读取及等待纳入资源保护；统一进程和读取任务收尾，输出回收限时 2 秒。清理失败仍回收读取任务并传播故障，原取消保留在异常上下文中。无法取得 psutil 身份时只回收本次直接子进程。
- REPL 删除 finished_reason、run_started 和渲染回写控制流程，依据等待结果处理继续、取消和致命错误；单次入口将 Ctrl+C 映射为 130。
- 最终完整回归：`.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider --basetemp=artifacts/run-flow-refactor-20261020/pytest-final --tb=short`，**317 项通过**，1 条既有 Pydantic ReadOnly 依赖警告。Ruff 规则、源码及测试格式、`git diff --check` 通过。Windows 沙箱临时目录权限受限，完整回归使用项目独立目录在沙箱外运行；未访问真实模型服务。
- 新增/迁移的测试覆盖受控结果、内部工具失败、显示器抛 SDK 同类型异常、模型流关闭、取消批次配对、清理失败、身份查询失败、输出读取失败及输出回收超时。清理失败后剩余工具和额外模型请求均为零；取消补充观察不再要求发送显示事件。
- 真实终端使用 `tty=true`，确认 stdin.isatty() 为 true，经过实际 CLI 与 REPL；模型脚本化，Bash 为本机 Git Bash。输入中 Ctrl+C 丢弃当前行，模型流和 Bash 执行中 Ctrl+C 后继续提问，最终 `/exit`。历史用户输入仅为 stream、bash、after；模型请求共 3 次，工具观察为 unknown，pending 为空；Bash Python 子进程 PID 6112 在应用结束时已不存在。单次入口中断捕获应用 SystemExit=130，会话捕获 SystemExit=0。
- 原始材料：`artifacts/run-flow-refactor-20261020/terminal_smoke.py`、`terminal-interactive/console.txt`、`terminal-interactive/report.json`、`terminal-once/report.json` 与 `terminal-verification.json`。11 项独立记录检查通过。TTY 的 PowerShell 宿主在 Ctrl+C 后报告退出码 1，故单独记录应用捕获的退出码，不把宿主退出码当作 CLI 退出码；单元 CLI 测试同时验证 130。第一次沙箱内 Bash 启动被拒绝，成功实验为沙箱外重跑。
- 运行说明和阅读顺序见 [执行流程](design/run-flow.md)，README、架构、M4a/M4b 设计与 PLAN 的有效契约已同步。本次提交保存实现、回归结果和终端证据摘要；M4b 仍待用户验收确认，不进入 M4c。

## 2026-10-20 M4b 终端取消接线与阶段性保存

- 用户手写 `ui/repl.py`：输入时 Ctrl+C 丢弃当前行；运行时只请求取消当前 Task 一次，等待其结束后再接受输入；正常返回以实际结果为准。每次运行重置取消意图、启动标记和结束类型，避免跨运行复用状态。
- 通过事件回调观察 `RunFinished`；仅用户取消且核心报告取消结束时正常续用，启动前取消由 UI 提示。外部取消继续传播；未正常报告的异常显示诊断并结束会话，核心已报告的 error/max_steps 不重复打印。所有退出路径恢复原 SIGINT handler。
- Codex 新增 9 项回归测试：输入中断、重复中断与清理等待、外部会话取消、后续 Task 启动前取消、真实 Agent 配合假模型的流取消后历史续用、三种意外异常，以及前次 cancelled 结束标记不影响下一次运行。信号入口被替换，测试未向 pytest 发送真实 SIGINT。
- 完整 `.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider`：304 项通过，1 条既有 Pydantic ReadOnly 依赖警告；Ruff 规则、源码及测试格式、git diff --check 通过。为避开 Windows 沙箱临时目录权限限制，完整测试在沙箱外运行，临时目录位于已忽略的 `artifacts/m4b-interruption-20261020-check/`，模型价格表使用 LiteLLM 本地副本。
- 本次只保存取消接线和自动化证据。真实终端中的输入、模型输出和 Bash 中断后继续尚未验证；Bash 清理失败经 Registry/Core 后是否保留取消语义仍需联动测试。完整 M4b 未验收，不进入 M4c。

## 2026-10-19 M4a 真实终端多轮会话与独立复核

- 用户指定 `C:/Users/1004945427/Desktop/pros/code_agent_target`，并明确授权将本次任务、靶子代码、测试、文档、命令输出与本地路径发送到当前 DeepSeek 服务。应用提交为 `6e3cc34`，模型 deepseek/deepseek-chat，max_steps=12，request_timeout_seconds=60。
- 原靶子留有先前修复；先备份原文件、diff 与固定材料哈希，仅将 math_utils.py 恢复到 m1-baseline。独立初始 pytest 为 4 项通过、1 项失败，退出码 1，失败为 average([]) 的 ZeroDivisionError。
- 在真实终端执行 `uv run codeagent`，未提供 -p；安装后入口 argv 为 codeagent.exe，stdin.isatty() 为 true。观察钩子只记录并转发原有调用，不替换输入或模型；没有修改应用源码。
- 实际输入空白、修复任务、后续追问、/exit。全程只创建一个 Agent，两次运行共 6 次模型请求（5 + 1）；7 次 Bash 调用与 7 个观察按 ID 各配对一次，最终无 pending calls。空白和退出不触发额外请求；两次运行正常结束，进程退出码 0。
- 第二次请求在第一次运行完整历史后追加本次用户输入，系统指令只出现一次。后续追问未重复此前的验收标记，模型没有调用工具，正确复述标记、修复内容及测试结果；末尾标记在终端记录中仅显示一次。
- 退出后独立 pytest：5 项通过，退出码 0；git diff --check 通过，仅 math_utils.py 修改，其他固定材料 SHA-256 均不变，没有新增未跟踪文件。保留靶子修复供用户检查，未提交或推送。
- 模型红测命令通过 echo 输出 EXIT_CODE=1，该 Bash 调用本身退出 0；独立测试直接捕获 pytest 退出码 1 → 0，不以工具 completed 或 CLI 退出码代替任务判据。
- 原始材料位于 `artifacts/m4a-session-20261019-run-001/`：原有修改备份、两条输入、初始/最终独立测试、console.txt、audit.jsonl、final-history.json、最终 diff、verification.json 和 report.md。16 项独立证据检查通过；临时观察不等同于 M5 轨迹功能。
- 本次文档提交前重新执行完整 `uv run pytest -q`：291 项通过，1 条既有 Pydantic ReadOnly 依赖警告；Ruff 规则检查与 git diff --check 通过。上述 5 项靶子测试与此处项目完整回归分别统计。
- 本次正常路径的真实会话外部复核通过，M4a 等待用户确认；没有测试失败、耗尽或 Ctrl+C 后继续，也不因此确认 M3 或进入 M4b/M4c。

## 2026-10-18 调整顺序：先多轮会话，再权限与审批

- 用户指出当前 CLI 只有 -p 单次入口，没有审批输入通道，要求先建立多轮会话。单次任务在技术上也可增加审批回调，但按当前学习目标，先完成持续交互更合适。
- M3 收窄为已经实现的执行环境校验、路径绑定和创建错误显示；原定但未完成的命令规则、执行前拒绝与人工审批整体后移到 M4c。此前讨论的 PolicyDecision / evaluate_bash_command 接口在 M4c 再确定，当前不接线；提交前仅同步 safety/policy.py 的阶段说明，运行逻辑保持不变。
- M4 分为 M4a 基础多轮输入与历史复用、M4b 失败/耗尽/取消后继续、M4c 命令权限判定与操作确认；M5–M8 编号和核心学习主线保持不变。
- 不为了进入 REPL 提前引入 Bash 解析器、缩小现有命令支持范围或实现模型审批。下一步先明确会话/运行/请求边界，再由 Codex 准备接口和关键测试，用户手写核心。
- 本次只调整文档与占位说明，不代表新能力已实现或阶段已验收；268 项通过引用同日已有回归，本次不以该历史结果宣称重新运行测试。下方同日原安排保留用于追溯，当前安排以 PLAN.md 为准。

## 2026-10-18 M3 执行环境校验与创建入口回归

- 用户实现执行环境数据类、目录与 Bash 路径校验、核心路径绑定及 CLI 创建错误显示；Codex 补测试，保留用户源码。2026-10-17 用户确认 M2b 验收并进入 M3。
- 新增 policy 测试 20 项，覆盖路径规范化、用户目录展开、显式相对路径与 PATH 同名文件、无效输入、错误诊断和异常原因保留；不启动真实 Bash。
- 新增核心创建入口与 CLI 启动错误测试 7 项，验证有效环境绑定、无效配置先于客户端构造、模型请求/工具执行零次，以及只显示错误事件一次。既有 CLI 测试改用临时目录与模拟可执行文件。
- 主仓库 `.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider --tb=short`：268 项通过，1 项既有 Pydantic ReadOnly 依赖警告；Ruff 规则检查、新增/变更测试文件格式检查及 git diff --check 通过。源码格式检查不计入这条通过声明。
- 下一步为选定危险命令的拒绝判定，再接入配对观察与结果事件；M3 整体尚未验收，未进行新的真实模型靶子验收。

## 2026-10-16 M2b 真实流式靶子运行与独立复核

- 用户要求 Codex 执行完整流程，并明确授权向当前 DeepSeek 服务发送本次独立靶子的任务、代码、测试、文档、命令结果和本地路径。应用版本 be7cfb1，模型 deepseek/deepseek-chat，max_steps=12，request_timeout_seconds=60.0。
- 复用本机可用的 M1 average 小任务基线，在 artifacts/m2b-streaming-20261016-run-001/target 创建独立 Git 靶子，保留原靶子的未提交修改；初始独立测试确认 4 项通过、1 项失败，average([]) 抛出 ZeroDivisionError。
- 通过现有 CLI 的 python -u -m code_agent.ui.cli -p 入口运行真实模型，16.64 秒后正常退出，退出码 0。模型读取任务与测试、复现失败、只修改 math_utils.py、运行测试并展示 diff；8 次 Bash 调用与 8 个结果按 ID 配对，最终总结标记出现一次，CLI 输出在结束前分多次到达。
- 模型结束后独立复跑测试：5 项通过；diff 仅涉及 math_utils.py，增加空序列返回 0.0 并更新 docstring。其他固定材料 SHA-256 全部不变，无新增未跟踪文件。靶子修改保留供检查，模型未提交或推送。
- 第一次模型测试命令在 pytest 后执行 echo，Bash 进程退出码为 0，输出 EXIT=1；模型正确识别失败。独立初始测试退出码为 1，最终独立测试退出码为 0，再次区分工具完成与任务达标。
- 原始记录和演示位于 artifacts/m2b-streaming-20261016-run-001/，含 case.json、prompt.txt、初始/最终测试、CLI 输出与到达时刻、最终 diff、verification.json 和 report.md；原始临时材料不纳入提交。stdout 读取块不等同于模型 chunk 或 HTTP 数据块。
- 本次正常路径的外部检查通过，未人为触发真实网络超时、断流或取消。M2b 等待用户确认，尚未进入 M3。

## 2026-10-16 M2b 模型请求超时实现与回归

- 用户实现 Settings.request_timeout_seconds：默认 60.0 秒，通过 gt=0 和 allow_inf_nan=False 限定为有限正数；沿用 CODE_AGENT_ 环境变量前缀。
- 用户在 LLMClient._request_kwargs 中映射为 timeout，流式与非流式请求共用；后续 kwargs 覆盖默认值，Settings 保持不变。Codex 仅对配置字段做格式整理。
- 完整回归 uv run pytest -q：241 项通过，1 项 Pydantic 对依赖 TypedDict 的 ReadOnly 限定符警告。Ruff 检查、两处变更源码的格式检查及 git diff --check 通过。
- 模拟 SDK 超时验证应用边界，不代表真实网络超时已验证。下一步准备独立靶子的初始红测，再完成真实流式代码修改、测试与 diff 复核；M2b 整体尚未验收。

## 2026-10-15 M2b 模型请求超时契约与失败测试阶段性保存

- 用户决定先保存进度。核心配置字段与参数透传尚未手写，不标记本小步或 M2b 已完成。
- 约定 Settings.request_timeout_seconds 默认 60.0 秒，要求有限正数，环境变量为 CODE_AGENT_REQUEST_TIMEOUT_SECONDS；LLMClient._request_kwargs 映射为 timeout，显式调用参数优先且不修改默认配置。
- 已核对当前 LiteLLM 1.103.2 的 SDK 路径与官方说明：本步采用 HTTP 等待超时，流式读取阶段限制等待数据的时间，不承诺整个请求或 Agent 运行的墙钟总时限；不新增总时长计时器或重试逻辑。
- 已更新配置校验、两种请求路径的默认值/透传/覆盖测试，使用 litellm.Timeout 验证请求前、部分分片后及结束分片后超时的原异常传播、不提交本轮、不执行调用与 CLI 错误去重。真实 SDK 异常类型仍通过 FakeLLM/AsyncMock 注入，不视为真实网络超时已验收。
- 当前完整测试共 241 项：224 项通过、17 项预期失败（9 项涉及配置字段/校验，8 项涉及 timeout 默认值或透传）。源码与测试 Ruff、格式检查及 diff 检查通过；未实现内容引起的失败明确保留，不能用上一提交的全绿记录替代。
- 下次先实现 config.py 的 request_timeout_seconds 字段，再在 llm/client.py 的请求参数中接入，保留 kwargs 覆盖优先级；复跑测试至全绿后，再准备真实流式靶子验收。

## 2026-10-15 M2b 工具结果与运行结束事件回归

- 接通 ToolResultEvent：正常及取消补齐的观察写入历史后发送，携带调用 ID、原始 ToolResult 状态和截断标志；UI 实时显示结果。
- 模型阶段失败发送 ErrorEvent 后发送 RunFinished(error)，继续抛出原异常；正常结束、取消和轮数耗尽分别发送 completed、cancelled、max_steps。结束事件每次运行只发送一次，位于本次事件序列末尾；取消时先补齐全部工具观察，再传播取消。
- Agent.run 使用局部 emit 集中处理可选回调；CLI 通过事件显示错误和收尾换行，避免重复输出正文、错误详情或额外换行。
- 新增 24 个测试用例并扩展既有测试：覆盖调用与结果时序、错误观察后继续、首/中/末工具取消、已知取消观察、断流/协议失败、正常结束及空正文、结果原样显示、错误去重和停止提示。完整回归由 199 项增至 223 项，全部通过；源码与测试 Ruff、格式检查及 git diff --check 通过。
- 测试使用假模型与模拟工具；本次未进行真实模型靶子验收。模型请求超时配置仍待实现，下一步先明确配置与透传契约；M2b 整体尚未验收，不进入 M3。

## 2026-10-14 M2b 终端事件阶段性保存

- 用户手写 Agent.run 的可选 on_event 回调：收到新增正文即发送 TextDelta；整轮消息校验并提交历史后，在每个工具执行前发送 ToolCallEvent。
- UI 的 render_event 用 Rich Text 原样显示正文、工具名称、调用 ID 和参数；CLI 传入回调，在正常/异常结束时换行，不再重放整份历史，避免正文重复显示。
- 文本事件已做 3 项临时专项验证，覆盖及时发送、断流不提交临时正文、原样渲染；正式测试尚未加入仓库。工具调用事件的位置与字段已静态核对，专项验证待补。
- 上次事件接线回归中，3 个 CLI 用例因工具过程/观察未显示而失败；当前已恢复调用显示，结果显示尚未接线。199 项全绿是此前流式循环的验收记录，不能作为当前整体通过的结论。
- 本次按用户要求保存进度，剩余工作留待换电脑后继续；不补新能力，不标记 M2b 完成。下次先定义 ToolResultEvent 并加入 AnyEvent，在正常及取消补齐观察写入历史后发送，再由 UI 渲染结果。
- 后续还需错误/停止事件、模型请求超时配置、正式事件与 CLI 回归测试、导入/未使用变量等 Ruff 清理，以及真实流式靶子验收。

## 2026-10-13 M2b 流式循环接线与回归

- 分片组装器和对应测试移入 llm/，组装与结构校验归模型层，历史提交、工具执行和任务结束由 core 编排。
- Agent.run 已接入 stream_turn；每轮流正常耗尽并通过 finish 校验后才提交历史，按调用 index 顺序执行并逐条回灌结果，有调用时继续请求，无调用的 stop 返回最终正文。
- 保留取消时的已知观察、unknown/not_started 配对和 max_steps；断流、协议失败或流中取消不提交本轮，不执行本轮工具。
- 现有 FakeLLM 和 CLI 测试已适配流式请求，新增 14 个流式场景，覆盖交错调用、耗尽前不提交或执行、结束片后断流、协议错误和失败后的历史续用。
- 完整回归 199 项通过；Ruff 检查、源码及测试格式检查、git diff --check 通过。测试使用假模型和模拟工具，模型价格表采用 LiteLLM 本地副本；临时产物保留在独立忽略目录。
- CLI 当前仍在运行结束后显示历史；终端事件、明确模型请求超时配置和真实流式靶子验收尚待完成，本次提交不代表 M2b 整体已验收。

## 2026-10-13 文档重整

- 主计划突出当前进度、稳定契约和待实施阶段；README、AGENTS.md 提供入口，历史细节集中到本文件。
- M2b 分为文本组装、工具分片、循环与事件接线；区分响应协议错误和完整调用的参数错误，明确临时显示与历史提交边界。
- M4 补充会话、运行、模型请求的生命周期及取消、步数上限、确认拒绝后的继续行为。
- M5b 从完整检查点恢复，保存白名单配置；会话恢复不承诺回滚磁盘副作用。M5/M6 区分完整历史与本次模型输入。
- 统一验收材料字段，按阶段逐步增加靶子难度；M7 仍负责自动化评测。
- M8 分为本地委派、MCP、A2A 三个验收点，本地协作增加相互矛盾结论的证据核实。
- 文档调整不代表后续能力已经实现。M2b 的组装实现进度见下条记录，流式循环与终端事件仍待接线；用户手写核心及逐阶段确认的分工保留。

## 2026-10-13 M2b 分片组装实现与回归

- 提交 139ad20：`feat(core): 实现流式响应分片组装`，新增 core/streaming.py 和对应测试。
- 文本按原样累积，工具参数按调用 index 追加；支持同片多个调用与交错片，正常收完后构造完整助手消息。
- 评审修正参数用 `=` 覆盖的笔误；按用户决定省略组装器的调用 ID 重复与冲突检查，对应测试同步调整，已有 History 配对约束保留。
- 组装测试 11 项通过，完整回归 185 项通过；Ruff 检查及相关源码和测试格式检查通过。完整回归因 Windows 临时目录权限限制在沙箱外运行，使用独立的已忽略临时目录。
- 真实 DeepSeek 文本流请求回答 `2`，观察到 3 个 JSON 事件和独立 `[DONE]`，HTTPX 返回 3 个响应体块；原始 smoke 产物保留在本机 artifacts 目录，提炼后的记录见 [流式协议研究](research/streaming-sse-boundaries.md)。
- 此步只完成组装，没有接入流式 Agent、终端事件或完成流式靶子验收，不能作为 M2b 整体完成证明。

## 迁出时的进度记录（2026-10-12）

> 2026-10-12 更新：参考 Datawhale Hello-Agents 补充阅读、实验与复盘安排；按用户补充要求，将记忆系统、多 Agent 协作与通信列为核心学习内容，主线扩展到 M8，保留用户手写核心的分工。
> 当前进度：M0、M1 已验收；M2a 的 ToolSpec / ToolRegistry、工具接线、ConversationHistory、Agent 历史接线与取消配对已实现。历史接线与取消配对见提交 fd1e4d1；原生结束协议与回归测试已实现，2026-10-12 完整回归 174 项通过，Ruff 检查及源码和测试格式检查通过。
> 2026-10-12 验收确认：run-003 的 multiply 已修复，独立 pytest 7 项通过；固定测试和材料未改，diff 仅包含 calculator.py，用户确认进入 M2b。
> 下一步：定义流式工具调用分片组装契约与关键测试，再由用户实现；finish_task 保持按需增强。

## 已完成阶段的历史记录

### M0 工程骨架与模型连通性〔已完成〕

已有配置、LiteLLM 接入、CLI 入口、事件与目录骨架、pytest/Ruff。历史验收是 --version 和最小文本响应；当前 CLI 已演进为 Agent 路径，不重复实施 M0。

### M1 最小编程任务闭环〔已验收〕

已有非流式循环、参数校验、错误回灌、最大请求轮数、异步 Bash、统一结果、工作目录绑定、输出预算、启动错误处理和 CLI 显示。最近完整基线为 117 项测试通过。

2026-10-10 独立复核 code_agent_target：只修改 math_utils.py 增加空列表返回 0.0，测试文件未改，5 项测试全部通过。用户确认进入下一阶段。基线标签 m1-baseline 保留，可用于重复验收。

**保留的 Bash 契约：**

- 模型参数只含 command 与 timeout_seconds；拒绝空白命令，超时默认 30 秒、范围 1–120 秒。
- 应用绑定 workspace_dir/bash_path，启动形式为 create_subprocess_exec(bash_path, "--noprofile", "--norc", "-c", command, cwd=workspace_dir, ...)。stdin 为 DEVNULL，stdout/stderr 为 PIPE，不静默切换到其他 shell。
- stdout/stderr 共享 20,000 字符保留预算；固定大小分块读取、增量 UTF-8 解码，超额内容仍排空但不保留。
- output 包含 stdout、stderr、原始 exit_code；正常非零退出也属于 completed，作为有效观察回灌。启动失败为 error；超时为 timed_out、exit_code=null。
- 超时或取消时尽力清理已发现的进程树；取消继续传播。现有 psutil 清理是后代快照，不宣称具备严格的进程组或 Job Object 隔离。

**M1 验收时的临时结束协议（M2a 已替换）：**

- 每轮先检查结束原因；length/content_filter 等异常不能作为成功结束。
- 同轮有工具调用时，保存文本与调用，执行全部调用并回灌结果，然后继续请求。该轮文本中的 END_TASK 不能结束任务。
- finish_reason=tool_calls 却无调用信息时明确报错。
- 无调用且 finish_reason=stop 时，忽略末尾空白后，最后一行严格等于 END_TASK 才结束并返回前面的总结。
- 普通中间文本保留在历史中直接继续，不额外生成 user 提示；仍受 max_steps 限制。
- 提示词要求先总结实际结果或说明阻塞原因，再输出独立末行标记。标记只声明结束，不证明任务成功。
- CLI 显示命令、结果和去标记的总结；失败时保留已有历史。CLI 返回 0 表示 Agent 正常结束，不等于任务的测试一定成功。

**已采用的验收流程，后续阶段复用：**

1. 新建独立目录与独立 Git 仓库，提供一个小 Python 模块和一个故意失败的 pytest 用例；不在 code_agent 源码目录生成验收产物。
2. 先人工确认红测；记录初始状态，防止通过删除、跳过或放宽测试伪造成功。
3. 设置工作目录与可用 Bash 路径，通过 -p 请求实现小功能；模型只用 Bash 读取、修改和运行测试。
4. 独立运行测试确认变绿，检查最终 diff，展示命令轨迹和退出码。
5. 只创建 Markdown 文档可作为连通性 smoke，不代替“改代码、跑测试”的验收。失败则记录具体失败点，修复后重验。

**完成标准：**上述闭环实际运行成功，用户确认验收结果。没有单轮工具调用数量上限这一前置条件。

### M2a 工具、历史与原生结束协议〔A/B/D/H，已验收〕

先沿用非流式响应整理协议，再进行流式改造：

已完成 tools/spec.py 的 ToolSpec 数据定义和 to_schema，以及 tools/registry.py 的 register/model_tools/execute；内置工具工厂与 Agent 已接入注册表，旧字典和 Agent 内部重复执行逻辑已移除。ConversationHistory 的四个公开方法已实现，Agent 与 CLI 已接入历史接口，取消时补齐调用观察并传播取消。工具层 16 项、历史模块 23 项及完整回归 156 项通过是接线前的记录；接线实现与新增测试见 fd1e4d1。原生结束路径已实现并补齐 None/空列表、空正文、原文保留、异常响应及步数边界测试，2026-10-12 完整回归 174 项通过；独立靶子 run-003 已复核 7 项测试通过、固定材料未改，用户已确认验收。

- register 拒绝重名注册，model_tools 按注册顺序生成定义，handler 不进入模型 schema。
- execute 接收 name 和 JSON 参数字符串，返回 ToolResult；未知工具、参数错误、执行错误统一为 ERROR，原因放入 output.error。
- 参数校验和执行异常分开处理；校验失败不调用 handler。已有 ToolResult 保留原状态与截断标志；旧字符串/JSON 输出包装为 completed。
- 支持异步函数、同步函数和返回 awaitable 的可调用对象；取消继续传播。核心保留执行环境绑定、调用 ID 配对、消息追加及序列化职责。
- 首个失败测试 test_execute_returns_structured_validation_error_without_running_handler：参数越界时返回可序列化错误结果，handler 不被执行。
- 内置工厂使用 partial 绑定规范化的 workspace_dir 与 bash_path；BashArgs 只暴露 command 和 timeout_seconds。Agent 使用 model_tools 生成定义，execute 返回结果经 model_dump_json 回灌到对应调用 ID；处理完本轮全部工具调用后继续请求模型。

- 从 Agent 中抽出 tools/registry.py、tools/spec.py：注册、schema、参数校验和统一执行结果；不预先实现 MCP 适配层。
- context/history.py 管理消息追加、工具调用/结果配对、任务中断后的有效历史；没有完成执行的调用不能留成可继续的悬空记录。
- ConversationHistory 已提供 add_message/get_messages/get_pending_calls/get_model_messages：消息与读取快照深拷贝；按调用 ID 登记和删除 pending；校验失败保持状态不变；存在未配对调用时可展示历史，但拒绝直接提供模型历史。pending 只表示尚未记录结果，不表示工具尚未执行。Agent 已通过 self.history 保存消息；取消时保留工具提供的观察，否则当前调用记为执行结果 unknown，剩余调用记为 not_started，不自动重跑结果未知的命令。
- finish_reason=stop 且本轮无工具调用时正常结束，返回最后一条助手正文；空正文返回空字符串，保留原有空白，END_TASK 仅作为普通文本。
- 本轮有调用时仍执行并回灌全部结果再请求模型；即使 finish_reason=stop 或正文声称完成，也不能在工具观察前结束。finish_reason=tool_calls 却无调用时明确报错，异常结束不执行工具，取消与最大步数分别停止。
- 循环结束与任务验收成功分开；工具 completed 不表示任务完成或测试通过。M2a 保持现有返回文本与异常路径，M2b 再按展示需求整理停止事件，不自动增加结构化任务结果或最后一轮额外模型请求。
- finish_task、completed/blocked 的模型申报及其混用规则按真实失败案例再设计，不作为 M2a 必做项；撤销原先以它为前提的接口/测试安排。最大请求轮数与超时保留，达到上限时仍走现有异常路径。
- 修复核心对终端显示的演示依赖。模型层继续保持薄封装，不把整个 SDK 响应类型复制成另一套模型。

**判据：**原靶子任务通过非流式路径完成；stop 无调用（含 None/空列表）、正文含旧标记、有调用但 stop、异常结束、tool_calls 无调用、步数上限和取消的 FakeLLM/CLI 测试通过。结束不要求文本标记，真实成功仍查看测试与 diff。

**已完成的实施小步：**

1. 验证用户手写的原生结束分支及相应回归测试：无工具的 stop 即返回，所有工具观察继续配对；移除 END_TASK 提示和解析。
2. 清理未使用的 ToolResultEvent 占位与核心终端演示依赖，流式事件接口留到 M2b 按需设计。
3. 完成 FakeLLM / CLI 回归与原靶子验收，展示证据并由用户确认进入 M2b；事件与结构化结果不能自动成为额外验收前置条件。

## 此前计划调整

### 2026-10-12：参考 Hello-Agents 的学习安排

- 保留已有 M0–M7 顺序，补充章节对应、理解问题和最小实验，再增加 M8；当前已有真实闭环，基础章节按需补，不从第 1 章重新排队。
- 更新 M2a 进度：历史接线和取消配对已有实现；按用户后续确认采用 stop 且无调用的原生结束机制，finish_task 后置为按需增强，流式事件在 M2b 设计。来源对照和推论边界见 [完成信号研究](research/agent-completion-signals.md)。
- M5、M6 分设验收小步，让轨迹、恢复、记忆、上下文、模式各有独立证据；原有模块边界和阶段编号保留。
- 提前积累失败案例，M7 再建设自动评测；模式比较增加相同条件下的实验，而不是假定更复杂的范式必然更好。
- 按用户补充要求，记忆生命周期、基础知识检索、多 Agent 协作与通信实验进入核心主线；向量数据库、ANP 完整实现、训练和大型案例保留为深化选题。此前将多 Agent 仅列为可选扩展的安排已撤销。

### 此前调整保留

- 撤销单轮工具调用数量上限：用户决定当前不需要，不能继续作为 M1 阻塞项。
- M1 直接进入真实任务验收：已有循环、执行器和 CLI，不再先增加与闭环无关的功能。
- M2 拆为协议整理和流式两步：先稳定工具/历史/结束契约，再处理分片，便于学习和定位问题。
- 模式协议延后到 M6：第二种实际模式出现后再抽象，减少预先设计接口的负担。
- 多轮会话提前到 M4，持久化与记忆顺延到 M5：交互不依赖长期记忆，先验证用户如何继续和取消任务。
- 拆开结束、成功、恢复与记忆的判据：避免用标记、日志或一句自评代替执行证据。
- 安全判据按实际保护能力收窄：保留必要策略，强隔离作为独立扩展，不作未经验证的越界保护承诺。
- 环境准备与实施进度分开记录：虚拟环境和可执行路径按各工作树核实，历史检查不再作为当前事实。
