# Codex、OpenCode 的运行结果与异常边界

研究日期：2026-10-20。本次重新读取官方公开源码：`openai/codex` 的 `main`、`anomalyco/opencode` 的 `dev`。下列行号对应本次网页返回的源码；这些是移动分支链接，后续可能漂移。本次未取得当前分支 SHA，也未运行上游项目，因此不把此前研究记录中的固定提交当成本次快照，不推断私有 Codex 桌面端实现。

## 结论

这两个项目没有消除异常或失败通道。可借鉴的是：**普通业务结果显式返回，错误按恢复边界分类，一次执行有确定的收尾入口，界面消费结果或生命周期事件。** 它们本身的层级和分支并不少，本项目无需复制其服务、协调器、事件存储或 Effect 框架。

## 官方实现事实

### Codex

- `SessionTaskResult` 为 `CodexResult<Option<String>>`：成功值、失败值有类型区分。任务仍可返回 `TurnAborted` 错误；不能据此声称它完全不用错误控制流程。任务接口同时接收 `CancellationToken`，另有 `abort()` 资源收尾入口。[tasks/mod.rs:72, 170–205](https://github.com/openai/codex/blob/main/codex-rs/core/src/tasks/mod.rs#L72)
- 启动位置统一等待任务结果，再调用 `on_task_finished()`；代码明确在此统一正常任务生命周期。取消 token 已触发时，由中断路径负责，避免再走普通完成流程。[tasks/mod.rs:362–397](https://github.com/openai/codex/blob/main/codex-rs/core/src/tasks/mod.rs#L362-L397)
- `on_task_finished()` 集中区分成功、`TurnAborted`、其他错误，并处理错误事件、活动任务和后续收尾。[tasks/mod.rs:610–649](https://github.com/openai/codex/blob/main/codex-rs/core/src/tasks/mod.rs#L610-L649)
- 工具边界显式区分 `FunctionCallError::Fatal` 与其他调用失败：fatal 转为运行级错误返回；其他失败转成模型能看到的工具响应。取消也有 token 监听和专门的 aborted 结果。[parallel.rs:94–105](https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/parallel.rs#L94-L105)、[parallel.rs:246–305](https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/parallel.rs#L246-L305)

适用启发：工具失败并非一律可反馈模型后继续；上层必须能区分普通观察和本次运行不能继续的失败。这不要求 Python 仿造 Rust 的整个类型系统。

### OpenCode

- Step 的业务决策用 `Outcome` 联合类型返回，包含 `Completed`、`Retry`、`Continue`、`RecoverFull`、`Compacted`、`OutputLimit`。失败、缺陷和中断仍通过 Effect 的失败通道传播。[step.ts:30–40](https://github.com/anomalyco/opencode/blob/dev/packages/core/src/session/runner/step.ts#L30-L40)
- 本地工具只用 `catchTag("Tool.Error")` 将指定工具错误结算为工具失败；用户拒绝、工具任务中断和缺陷另行分类。它不是对所有未知错误无条件生成普通工具观察。[step.ts:116–125](https://github.com/anomalyco/opencode/blob/dev/packages/core/src/session/runner/step.ts#L116-L125)、[step.ts:280–303](https://github.com/anomalyco/opencode/blob/dev/packages/core/src/session/runner/step.ts#L280-L303)
- Step 在受保护的最终收尾区等待/中断工具，为未结算调用补结果，并传播无法恢复的流、工具或 Step 失败。业务恢复和异常传播同时存在。[step.ts:132–144](https://github.com/anomalyco/opencode/blob/dev/packages/core/src/session/runner/step.ts#L132-L144)、[step.ts:205–268](https://github.com/anomalyco/opencode/blob/dev/packages/core/src/session/runner/step.ts#L205-L268)
- 协调器通过 `onExit()` 对每种退出调用同一 `settled` 钩子，钩子完成后再结算 `done`；正在 stopping 时，新运行等待旧 execution 结算。[run-coordinator.ts:92–124](https://github.com/anomalyco/opencode/blob/dev/packages/core/src/session/run-coordinator.ts#L92-L124)
- execution 层把 `Exit` 映射为 `succeeded / interrupted / failed`，再在集中钩子发布对应生命周期事件。这里的 `terminal()` 优先识别含中断的 cause；不能据此推断“清理失败必定优先成为 failed”。[execution.ts:45–49](https://github.com/anomalyco/opencode/blob/dev/packages/core/src/session/execution.ts#L45-L49)、[execution.ts:113–141](https://github.com/anomalyco/opencode/blob/dev/packages/core/src/session/execution.ts#L113-L141)
- Shell 本身返回 exit、timeout、status 等结构化结果；取消钩子调用 job/shell 清理，部分钩子使用 `Effect.ignore`。这说明上游存在具体的清理容错策略，不证明任意清理错误都应忽略。[shell tool:58–70](https://github.com/anomalyco/opencode/blob/dev/packages/core/src/tool/plugin/shell.ts#L58-L70)、[shell tool:221–247](https://github.com/anomalyco/opencode/blob/dev/packages/core/src/tool/plugin/shell.ts#L221-L247)

适用启发：以统一收尾保障执行生命周期完整；无需让 UI 从渲染成功与否推断执行是否完成。本项目只需借鉴这一职责边界，无需增加协调器层。

## 对 code_agent 的候选设计（本项目推论，未实施）

保留现有调用链：`REPL → Agent.run → Registry → run_bash`。只调整返回值、异常边界和私有收尾函数。

| 情况 | 负责层 | 对上层的表达 | 后续动作 |
|---|---|---|---|
| 模型正常完成、轮数耗尽 | Agent | 返回轻量 `RunResult` | REPL 按结果继续输入 |
| 已知模型服务或协议失败 | 模型适配/Agent 边界 | 明确识别后生成受控失败结果 | 当前运行停止，允许下一次输入 |
| 未知工具、参数错误、命令非零退出、已妥善清理的超时 | Registry/工具 | `ToolResult` | 模型获得观察，按现有循环继续 |
| 用户取消 | 原生 asyncio 取消链 | `CancelledError` | core 收尾后重抛；REPL 仅处理自身发起的运行取消 |
| 未知程序错误、资源清理失败 | 发生点向外传播 | 普通异常保留原因链 | 当前运行及会话停止，明确报错 |

`RunResult` 可仅含 `reason=completed/failed/max_steps`、`text`、可选诊断。名称可复用已有 `RunFinished.reason`；事件用于显示进度，返回值用于调用方判断结果。不要同时维持两套可独立修改的终态；终态事件由最终结果派生。

具体可简化四处：

1. **Registry 缩窄捕获。** 删除把所有 handler `Exception` 都包装成 `ToolResult.ERROR` 的兜底。工具主动返回可恢复结果；参数校验只捕获自己的 `ValidationError`。如确有跨工具共用的预期失败才引入一种专用工具异常，暂不建立异常类树。
2. **Agent 只在一个外围处理结束。** 主循环决定业务结果；一个私有收尾函数负责结清历史中的 pending calls、保留已知观察并生成终态。取消和普通 fatal 也经过同一收尾逻辑，之后继续传播各自异常。不要在每次模型请求和每次工具调用重复发布 `RunFinished`。
3. **Shell 拥有资源。** `run_bash` 负责进程和输出任务清理；`stop_process_tree` 保持底层职责。清理失败应停止本次运行，保留“发生于用户取消期间”的异常原因链；无需为了绕过 Registry 的宽捕获，把每种清理错误都变成一种携带结果的取消异常。
4. **REPL 依据等待结果。** 接收 `RunResult`；仅捕获自己发起的 `CancelledError` 继续输入，外部取消继续传播，未知错误结束会话。移除依赖渲染回调收集 `finished_reason` 再判断是否可继续的逻辑。取消发生在任务尚未开始时，等待该 task 仍会得到取消，不必由 `run_started` 再推断核心成功与否。

这里“清理失败停止会话”是本项目的简单策略，原因是不能确认资源已结清；不是两个参考项目共有的硬性做法。为避免继续扩大异常协议，优先让窄捕获恢复普通异常传播，再决定是否确实需要附带观察的取消异常。

## 测试边界

现有新增测试的核心价值应保留：取消期间清理失败后，**剩余工具执行次数为零、模型不再请求、失败原因可见、不会继续新一轮输入**。如果采用上述设计，“最终必须抛 `CancelledError`”不是必要条件，应改为验证 fatal 与原取消的原因链；正常取消仍验证 `CancelledError` 传播与完整历史配对。业务结果测试则直接断言 `RunResult`，避免通过渲染事件加异常组合推断结果。

本次仅研究和提出候选接口，未修改源码、测试或里程碑。
