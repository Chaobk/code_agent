# Agent 运行流程：保持浅层调用，集中结束与收尾

日期：2026-10-20。本文保留最初研究与候选设计；用户随后确认直接循环、明确结果、集中收尾，并授权 Codex 完成本次重构。最终实现见 [运行流程](../design/run-flow.md)：RunFinished/ErrorEvent 已删除，进度事件只保留三类，运行结束由返回值与外层处理表达。本文后续的保留终态事件建议已被最终方案替代，不表示 M4b 已验收。

## 结论

保留现有 `REPL → Agent.run → Registry → Bash` 调用链。借鉴 mini-swe-agent 的直接循环与集中处理，借鉴 Codex / OpenCode 对工具失败、运行终止和资源收尾的区分。现阶段不增加 SessionManager、RunController、协调器、通用状态机或取消令牌框架。

本项目可先做三项实质调整：

1. 给一次运行一个轻量的明确返回值，正常完成、请求失败和步数耗尽不再都通过异常表达。
2. Registry 只把约定的工具错误转换为观察，内部缺陷与资源清理失败继续向外传播。
3. 核心集中补齐未结算调用并发布结束信息，UI 不再通过渲染事件反推能否继续。

这些是本项目的设计推论，并非三个参考项目共同采用的同一种实现。

## 参考实现的事实与边界

### mini-swe-agent

默认流程直接组合 `run → step → query / execute_actions`，环境执行与模型观察格式化各有清楚入口。`run` 集中处理 `InterruptAgentFlow`，最终通过 exit 消息返回结果。它本身使用异常进行流程跳转，包括完成与额度耗尽；不能将其描述成“不使用异常控制”。[官方控制流文档](https://mini-swe-agent.com/latest/advanced/control_flow/)、[已核实提交的默认 Agent 源码](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/agents/default.py)

适合借鉴的是短主循环、集中边界与直接数据传递。同步执行与较弱的中断清理契约也减少了需要处理的情况；不能仅删除本项目的异步取消和历史配对分支来追求相同行数。版本、原生工具调用及交互模式的具体证据见 [专项核查](mini-swe-agent-exceptions.md)。

### Codex

所核对的 main 源码中，任务返回 `SessionTaskResult = CodexResult<Option<String>>`；创建任务的位置取得结果后统一调用 `on_task_finished`。取消 token 走单独的中断生命周期。工具层明确把 `FunctionCallError::Fatal` 向上传播，其他工具错误才转换为模型可见的失败响应。[任务入口与统一结束](https://github.com/openai/codex/blob/main/codex-rs/core/src/tasks/mod.rs)、[工具错误转换](https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/parallel.rs)

可借鉴的是结果分类和生命周期所有权。Rust 的 Result 仍有错误传播语义，不等于消除了分支；当前项目不需要照搬完整任务、协议和扩展层。

### OpenCode

本次核对的 dev 源码用 `Outcome` 表达单步的 Completed、Retry 等业务结果，用 `Exit` 汇总执行成功、失败与中断，再经 `settled` 统一发布执行终态。工具执行只捕获明确的 `Tool.Error` 转成观察，取消与其他失败保留各自传播路径。[单步结果与工具错误](https://github.com/anomalyco/opencode/blob/dev/packages/core/src/session/runner/step.ts)、[执行终态](https://github.com/anomalyco/opencode/blob/dev/packages/core/src/session/execution.ts)、[统一收尾](https://github.com/anomalyco/opencode/blob/dev/packages/core/src/session/run-coordinator.ts)

详细核查见 [专项核查](codex-opencode-run-lifecycle.md)。本项目采用“由执行端结算结果”的原则，保留既有“等待旧运行收尾后再允许新运行”的行为；客户端、服务端和执行协调层不纳入此次方案。

Codex main / OpenCode dev 是本次读取的移动分支快照，链接之后可能变化；不能将它们与旧研究笔记的固定提交混为同一版本，也不能仅凭这些源码断言桌面安装版本的实际行为。

## 重构前的复杂度与调整理由

| 重构前做法 | 带来的问题 | 调整方向 |
|---|---|---|
| `Agent.run` 返回文本，同时抛异常、发送结束事件 | UI 要组合多种信号判断结果 | 受控结束返回明确结果；事件用于展示和记录 |
| REPL 在渲染回调中记录 `finished_reason` | 渲染是否成功影响业务状态判断 | 删除这条反向控制链 |
| `run_started` 与结束事件共同决定取消后能否继续 | 状态组合增多，启动前取消要额外分支 | 依靠核心收尾契约；启动前取消没有产生历史或工具副作用 |
| Registry 对 handler 使用 `except Exception` | 清理失败、程序错误被降级成模型可修正的观察 | 只转换明确约定的工具错误 |
| 模型取消、工具取消、错误和耗尽分别发送终态 | 容易漏发、重复或时序不一致 | 在核心结束边界集中处理 |
| 取消时才补齐 pending | 其他中途失败也可能留下悬空调用 | 共用一个核心结算入口 |

依据：[核心循环](../../src/code_agent/core/agent.py)、[Registry](../../src/code_agent/tools/registry.py)、[REPL](../../src/code_agent/ui/repl.py)、[Bash](../../src/code_agent/tools/builtin/shell.py)。

## 最小的返回值与处理规则

`Agent.run` 可返回一个简单 dataclass；字段先只需要 `status`、`text`、`error`。状态为 `completed`、`failed`、`max_steps`，不同时再引入 `success`、`done`、`recoverable` 等重叠标志。

| 情况 | 表达方式 | 下一步 |
|---|---|---|
| 正常结束、空正文结束 | `RunResult(completed)` | 回到输入 |
| 步数耗尽 | `RunResult(max_steps)` | 回到输入，新运行重新计数 |
| 已分类的模型请求失败或响应协议错误 | `RunResult(failed)` | 丢弃未提交响应，收尾后回到输入 |
| 未知工具、非法参数、已约定的文件读取错误 | `ToolResult(ERROR)` | 作为观察交给模型，本次运行可继续 |
| Bash 非零退出 | 保留退出码的 `ToolResult(COMPLETED)` | 模型根据输出决定下一步 |
| Bash 超时且清理成功 | `ToolResult(TIMED_OUT)` | 作为观察交给模型 |
| 用户取消且清理成功 | 原生 `CancelledError` | 核心结算后由 REPL 消费自己发起的取消 |
| 外部取消会话 | 原生 `CancelledError` | 继续向上传播 |
| 清理失败、历史不变量损坏、内部程序错误 | 异常向外传播 | 诊断并结束会话，不继续执行命令 |

取消继续遵循 Python 的 Task 语义，避免吞掉外部取消。`RunResult` 不必为了形式统一而接管取消。原生取消有助于结构化并发和外部调用方正确停止任务。[Python 3.12 取消文档](https://docs.python.org/3.12/library/asyncio-task.html#task-cancellation)

“已分类”需要明确边界：模型调用阶段只捕获已约定的 SDK 失败；组装器可用一个明确的协议异常类型表达无效响应。不要把任意 `RuntimeError`、`TypeError` 或渲染错误都归入可恢复的模型失败。原始异常链用于诊断，分类在所属边界完成。

## 每层只负责什么

### Bash：资源所有权

启动的进程、stdout/stderr 读取任务由 Bash 执行器负责收尾。超时与取消共用必要的资源回收逻辑；等待需要有边界，清理失败后也要回收读取任务。

清理成功时传播原取消。清理失败可以抛一个明确的资源清理异常，并用异常链保留原取消；Registry 不把它转换成普通工具观察。这样取消发生过、清理未完成两件事都能保留，且运行必定停止。这里的“致命”指本次会话停止，不表示进程必然崩溃。

此前建议用 `ToolExecutionCancelled` 携带清理失败属于局部修补方案。整体采用此设计后，清理失败与正常取消分开，避免将“用户取消已收到”误当成“资源可以安全复用”。本项目仍只承诺对已发现进程尽力清理。

### Registry：查找、校验、调用、包装

保留未知名称和参数校验的 `ToolResult`。handler 的可预期失败可由工具自身返回结果，或用一个明确的工具错误类型传出并在此转换；两条路线选定一种约定即可。已有 `read_file` 等直接抛出的预期文件错误需要随接口调整，不能只删除兜底捕获而让正常的文件不存在也终止会话。

取消、资源清理异常和内部程序缺陷不在这里吞掉。无需为每个错误消息再造一个异常子类。

### Agent：直接循环与一个收尾边界

保持“请求并组装 → 提交助手消息 → 顺序执行并写入观察 → 继续或结束”的直接结构。

只在确实能消除重复时抽取私有函数，例如 `_settle_pending`；不要求为了层次整齐再增加一套 runner。

统一收尾负责保留已提交观察，为当前未知结果和未启动调用补观察，保持配对完整；受控结束返回 `RunResult`，取消和致命异常继续传播。最终采纳的实现不再生成运行终态事件，执行知识留在 core，History 维护配对和快照。

这条结算路径也覆盖普通中途错误。结算自身失败应停止会话，不绕过 History 校验。流式文本与工具事件继续即时显示；显示回调失败属于终端故障，先做必要结算并退出，不反复向同一个失败回调发送错误事件。

### REPL：输入、信号、等待、显示

保留当前 Task、输入状态和本次用户取消意图。重复 Ctrl+C 不重复打断清理。

等待返回结果时显示一次结束原因并继续；捕获取消时区分本地取消意图与会话外部取消；未预期异常诊断后结束。删除 `finished_reason` 事件窥探和依赖它的 `run_started` 判断。

原候选方案曾保留 `RunFinished` 通知；最终重构删除了该事件。UI 通过等待结果与外层异常处理显示一次结束信息，渲染器不回写业务状态。

## 建议实施顺序

1. 先确认上表的结果语义，尤其清理失败应终止会话。
2. 调整 Registry 的转换范围，并补齐普通工具错误仍可修正的测试。
3. Bash 完成资源收尾，区分正常取消与清理失败。
4. Agent 引入轻量返回值并集中 pending 结算，CLI 单次入口同步映射退出码。
5. REPL 删除事件窥探与重复状态，保留必要的信号适配。
6. 跑 FakeLLM、工具、CLI 与真实终端验收，最后再更新 PLAN 的有效契约。

测试以行为为中心：取消/致命失败后不执行后续动作、工具观察配对完整、流式临时内容不入历史、已分类错误后可继续、内部错误后退出、清理不泄漏任务。近期新增的“清理失败必须仍抛 CancelledError”断言约束了局部实现；若采用此候选契约，应改为允许明确的致命清理异常，同时保留“剩余工具零次、模型零次额外请求、诊断可见”的核心断言。不能为了让旧异常类型断言通过而掩盖清理失败。

这是对已有契约的候选调整；先由用户确认设计，再按项目分工准备接口/测试，由用户实现核心。
