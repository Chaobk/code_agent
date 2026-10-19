# 编程 Agent 的终端中断：Codex 与 OpenCode

研究日期：2026-10-19。依据官方文档和固定提交的公开源码，未运行这两个源码快照做终端测试。以下行为是所查开发版本的实现，不等于所有发布版本、用户自定义快捷键或桌面 App 的行为。本文只记录研究与候选设计，不修改本项目源码或扩大 M4b 验收范围。

## 1. 与当前问题最相关的结论

两套实现都把“输入按键”“请求停止当前运行”“异步工作收尾”“退出应用”区分开。它们有运行状态、可中断的执行单元，以及取消后发布终态的流程；不能把停止实现概括为为模型开一个子线程，然后强行终止线程。Codex 使用 Tokio task 和 CancellationToken；本次 OpenCode 开发快照使用 Effect Fiber。详细证据见后文固定源码链接。

共同结构可概括为下面的分析图；它是对两套代码的归纳，不是它们共用的实际接口名称。

```mermaid
flowchart LR
    A[终端按键或停止按钮] --> B[UI 判断输入和运行状态]
    B --> C[向当前运行发中断请求]
    C --> D[模型流与工具响应取消]
    D --> E[进程清理和记录收尾]
    E --> F[发布中断终态]
    F --> G[会话接受后续输入]
```

退出应用是另外一个动作；中断也不撤销已完成的写文件、提交或外部副作用。看到取消请求的 API 成功返回，不能直接推断所有清理已结束。两套实现对时序、在途工具和终态记录都有专门代码。

## 2. 对 code_agent 的建议（推论，尚未实施）

当前项目的 `Agent.run()` 已有异步模型流、取消传播、工具清理及历史配对；会话仍同步读取输入并直接 await 运行。适合先复用这些核心契约，在 UI/会话层增加一次运行的 Task 和终端中断接线。暂时不需要复制它们的客户端/服务器分层、完整 TUI、Effect 或取消令牌系统。[本项目有效计划](../../PLAN.md)、[当前会话入口](../../src/code_agent/ui/repl.py)

| 学到的边界 | 当前 Python 项目的候选做法 |
|---|---|
| 会话与一次运行分开 | 同一个 Agent 保留历史；每次有效输入创建一个 `asyncio.Task` 执行 `Agent.run()` |
| 按键与业务中断分开 | 终端 Ctrl+C 转为对当前运行的取消请求，UI 不直接修改历史 |
| 发出请求与清理完成分开 | `task.cancel()` 后仍等待任务结束，之后再显示输入提示符 |
| 中断与退出分开 | 输入等待时 Ctrl+C 丢弃当前输入；运行时取消当前运行；`/exit`、EOF 结束会话 |
| 当前运行取消与外部会话取消分开 | 只处理自己发起的运行取消；外部取消继续传播，不用 `except BaseException` 吞掉 |
| 历史与外部副作用分开 | 保留已知结果，未知执行不自动重跑；不因 UI 返回提示符就假设文件未被修改 |

`asyncio.create_task()` 返回可调度、可等待、可取消的异步任务对象。它没有为这次运行创建 OS 子线程；`.cancel()` 是请求，协程有机会在后续执行点收到 `CancelledError` 并做清理。[Python Task 文档](https://docs.python.org/3.12/library/asyncio-task.html#creating-tasks)、[取消语义](https://docs.python.org/3.12/library/asyncio-task.html#task-cancellation)

`asyncio.run()` 默认在 Ctrl+C 时取消主任务，因此只拆 Task 还不够；要明确哪一层接收 SIGINT、取消哪个 Task，以及退出时如何恢复原信号处理器。Windows 不支持 `loop.add_signal_handler()`，终端适配不能只照搬 Unix 示例。`signal.signal()` 需要在主线程设置。保持现有同步 `input()` 时，普通输入与运行阶段分开处理；不必为了接一次中断就提前引入输入线程。[Runner 的 Ctrl+C 处理](https://docs.python.org/3.12/library/asyncio-runner.html#handling-keyboard-interruption)、[Windows 限制](https://docs.python.org/3.12/library/asyncio-platforms.html#windows)、[信号与线程](https://docs.python.org/3.12/library/signal.html#signals-and-threads)

建议后续手写顺序：先创建并等待运行 Task，再确定终端信号适配与取消来源，最后覆盖取消清理期间的重复按键、外部取消和退出时恢复信号处理器。重复 Ctrl+C 应先约定清理期间如何处理，避免二次取消打断现有工具清理；不照搬 Codex 的 100ms 宽限期或 OpenCode 的按键次数。

验收继续使用现有 M4b 判据：输入中 Ctrl+C 后能重新输入；模型流和工具执行中取消后可继续；工具观察配对完整；未提交分片不污染下一次输入；unknown 命令不自动重跑；真实终端验证与 `task.cancel()` 自动化测试分开统计。保存本文不表示这些能力已实现。

## 3. Codex 源码证据

研究日期：2026-10-19。官方 `openai/codex` main 固定提交 `7c073196a1673a0b248b2e5edfb7548d2c2290ac`，提交时间 2026-10-19T14:34:11Z。这是公开 CLI/TUI 与 core/app-server 的静态源码调查，没有运行该版本的交互测试，不覆盖私有桌面 UI。

### 按键不直接等于进程退出

TUI 初始化开启 raw mode，并有独立的终端按键事件类型；Ctrl+C 在聊天界面按键路由中进入 `on_ctrl_c()`。[终端模式](https://github.com/openai/codex/blob/7c073196a1673a0b248b2e5edfb7548d2c2290ac/codex-rs/tui/src/tui.rs#L242-L260)、[按键入口](https://github.com/openai/codex/blob/7c073196a1673a0b248b2e5edfb7548d2c2290ac/codex-rs/tui/src/chatwidget/interaction.rs#L184-L193)

底部视图优先消费取消键：关闭弹窗、取消搜索、清空非空输入。没有被消费时，若有可取消工作，提交中断；否则退出。当前 `DOUBLE_PRESS_QUIT_SHORTCUT_ENABLED=false`，不能根据仍保留的双击退出代码就声称当前默认需要按两次。默认 `interrupt_turn` 是 Esc；它也受弹窗等状态影响。[输入框分支](https://github.com/openai/codex/blob/7c073196a1673a0b248b2e5edfb7548d2c2290ac/codex-rs/tui/src/bottom_pane/mod.rs#L1001-L1052)、[运行/退出分支](https://github.com/openai/codex/blob/7c073196a1673a0b248b2e5edfb7548d2c2290ac/codex-rs/tui/src/chatwidget/interaction.rs#L578-L649)、[双击开关](https://github.com/openai/codex/blob/7c073196a1673a0b248b2e5edfb7548d2c2290ac/codex-rs/tui/src/bottom_pane/mod.rs#L242-L248)、[Esc 默认绑定](https://github.com/openai/codex/blob/7c073196a1673a0b248b2e5edfb7548d2c2290ac/codex-rs/tui/src/keymap.rs#L1690-L1696)

### 从界面到核心

`AppCommand::Interrupt` 转为带 thread/turn 标识的 `turn/interrupt` 请求；app-server 把它交给核心 `Op::Interrupt`。核心处理该操作后不退出提交循环。官方协议把 thread 定义为对话、turn 定义为一次用户请求及后续工作，取消后 turn 的状态为 interrupted。[TUI 请求映射](https://github.com/openai/codex/blob/7c073196a1673a0b248b2e5edfb7548d2c2290ac/codex-rs/tui/src/app/thread_routing.rs#L668-L700)、[服务端处理](https://github.com/openai/codex/blob/7c073196a1673a0b248b2e5edfb7548d2c2290ac/codex-rs/app-server/src/request_processors/turn_processor.rs#L1622-L1665)、[核心提交循环](https://github.com/openai/codex/blob/7c073196a1673a0b248b2e5edfb7548d2c2290ac/codex-rs/core/src/session/handlers.rs#L497-L506)、[官方协议](https://learn.chatgpt.com/docs/app-server#interrupt-a-turn)

每次运行建立 `CancellationToken`，把子 token 传给运行任务，并用 `tokio::spawn` 调度。中断处理先取消 token，等待完成通知或 100ms 宽限期，再调用任务 handle 的 abort 和任务特定的清理入口。此后按配置记录中断历史标记并发送 `TurnAborted`。100ms 是此快照的实现值，不是本项目应照抄的超时或所有进程清理的时限。[创建 token/运行任务](https://github.com/openai/codex/blob/7c073196a1673a0b248b2e5edfb7548d2c2290ac/codex-rs/core/src/tasks/mod.rs#L325-L433)、[100ms 常量](https://github.com/openai/codex/blob/7c073196a1673a0b248b2e5edfb7548d2c2290ac/codex-rs/core/src/tasks/mod.rs#L70-L72)、[取消与终态](https://github.com/openai/codex/blob/7c073196a1673a0b248b2e5edfb7548d2c2290ac/codex-rs/core/src/tasks/mod.rs#L943-L1075)

### 模型与工具

模型请求建立及逐事件读取都监听取消 token；读取取消后走 `TurnAborted`，取消与其他抢占分支不同。这证明本地接收与后续处理可停止，不足以承诺供应商服务器计算立即停止。[建立流](https://github.com/openai/codex/blob/7c073196a1673a0b248b2e5edfb7548d2c2290ac/codex-rs/core/src/session/turn.rs#L2715-L2748)、[读取流取消](https://github.com/openai/codex/blob/7c073196a1673a0b248b2e5edfb7548d2c2290ac/codex-rs/core/src/session/turn.rs#L2791-L2828)

工具调度监听 token，部分 handler 自行完成取消收尾，其他调用会 abort 其调度任务，等待结束并生成中断结果和通知。工具取消不是简单地从屏幕上隐藏输出。[工具取消](https://github.com/openai/codex/blob/7c073196a1673a0b248b2e5edfb7548d2c2290ac/codex-rs/core/src/tools/parallel.rs#L259-L315)

进程执行层有单独的取消处理：`ExecExpiration` 支持取消 token；所查管道进程路径会尝试终止进程组、短暂等待，再升级强杀并收集输出。Unified exec 另外封装本地/exec-server 进程终止。这些是不同执行路径的机制，不能仅凭这些文件推断所有后台终端都会在 turn 中断时关闭，也不保证所有平台和外部后代都被同样处理。[取消型 expiration](https://github.com/openai/codex/blob/7c073196a1673a0b248b2e5edfb7548d2c2290ac/codex-rs/core/src/exec.rs#L150-L201)、[进程组清理](https://github.com/openai/codex/blob/7c073196a1673a0b248b2e5edfb7548d2c2290ac/codex-rs/core/src/exec.rs#L1027-L1083)、[独立进程终止](https://github.com/openai/codex/blob/7c073196a1673a0b248b2e5edfb7548d2c2290ac/codex-rs/core/src/unified_exec/process.rs#L235-L268)

### 与 Python 项目的对应（推论）

可借鉴的是“会话持有历史、一次运行有取消边界、取消向模型/工具传播、收尾后报告终态”。Rust Tokio task 不应翻译成“为每次模型请求开一个 OS 子线程”；源码展示的是异步任务调度，本文不据此断言 Codex 整个进程只使用一个线程。对当前已有异步核心的 Python 项目，`asyncio.Task` 可以承担一次运行的控制对象，终端信号接线另行设计。

## 4. OpenCode 源码证据

研究日期：2026-10-19。官方仓库：anomalyco/opencode。固定 dev 提交：`815de9950b205cd734c9962c4629b07d3b83ee07`，GitHub API 返回提交时间 `2026-10-19T14:58:52Z`；该提交 `@opencode/core` 版本为 `2.0.32`。这是源码静态阅读，没有实际启动 OpenCode 测试终端行为。[版本来源](https://github.com/anomalyco/opencode/blob/815de9950b205cd734c9962c4629b07d3b83ee07/packages/core/package.json#L1-L10)

### 结论

OpenCode 当前 TUI 的默认“停止当前运行”按键是 **Esc**；Ctrl+C 负责输入清空或应用退出。中断沿会话接口传到核心，由核心中断本轮执行的 **Effect Fiber**，随后关闭模型流、取消工具并发布结束事件。它不是“按 Ctrl+C 就杀掉模型线程”。

### 1. 三个用户动作分开

| 动作 | 默认按键及代码行为 | 证据 |
|---|---|---|
| 清空输入 | Ctrl+C；输入非空且输入层生效时启用 `prompt.clear`，调用 `clearPrompt()` | [键位](https://github.com/anomalyco/opencode/blob/815de9950b205cd734c9962c4629b07d3b83ee07/packages/tui/src/config/keybind.ts#L201-L208)、[处理](https://github.com/anomalyco/opencode/blob/815de9950b205cd734c9962c4629b07d3b83ee07/packages/tui/src/component/prompt/index.tsx#L418-L429)、[条件](https://github.com/anomalyco/opencode/blob/815de9950b205cd734c9962c4629b07d3b83ee07/packages/tui/src/component/prompt/index.tsx#L954-L960) |
| 中断当前会话运行 | 默认 Esc；运行中、输入聚焦、自动补全关闭时，第一次设置确认状态；5 秒内第二次调用 `session.interrupt({sessionID, resume:true})` | [默认键位](https://github.com/anomalyco/opencode/blob/815de9950b205cd734c9962c4629b07d3b83ee07/packages/tui/src/config/keybind.ts#L125-L130)、[处理](https://github.com/anomalyco/opencode/blob/815de9950b205cd734c9962c4629b07d3b83ee07/packages/tui/src/component/prompt/index.tsx#L500-L530) |
| 退出 TUI | `app.exit` 默认 Ctrl+C、Ctrl+D、leader+q；执行 `exit()`。焦点、弹窗等更具体的键层可先消费按键 | [默认键位](https://github.com/anomalyco/opencode/blob/815de9950b205cd734c9962c4629b07d3b83ee07/packages/tui/src/config/keybind.ts#L45-L50)、[处理](https://github.com/anomalyco/opencode/blob/815de9950b205cd734c9962c4629b07d3b83ee07/packages/tui/src/app.tsx#L1140-L1146) |

TUI 还显式设置 `exitOnCtrlC:false`，不让渲染器直接套用默认 Ctrl+C 退出行为；由应用键位决定动作。[配置](https://github.com/anomalyco/opencode/blob/815de9950b205cd734c9962c4629b07d3b83ee07/packages/tui/src/app.tsx#L235-L249)

Shell 输入模式是额外状态：Esc 返回普通模式；空输入时 Ctrl+C 也只返回普通模式。[源码](https://github.com/anomalyco/opencode/blob/815de9950b205cd734c9962c4629b07d3b83ee07/packages/tui/src/component/prompt/index.tsx#L989-L1004)

这里的“Shell 输入模式”是编辑提示词的模式，不等同于已经运行的 Shell 子进程。

### 2. 从按键到取消当前运行

```text
TUI Esc × 2
  → client.api.session.interrupt(sessionID, resume=true)
  → 服务端 session.interrupt
  → SessionExecution.interrupt
  → SessionRunCoordinator.interrupt
  → Fiber.interrupt(execution.owner)
  → 模型流结束、工具取消、最终事件发布
```

服务端 `session.interrupt` 处理器调用会话服务；会话服务转到执行服务。[服务端](https://github.com/anomalyco/opencode/blob/815de9950b205cd734c9962c4629b07d3b83ee07/packages/server/src/handlers/session.ts#L590-L595)、[会话服务](https://github.com/anomalyco/opencode/blob/815de9950b205cd734c9962c4629b07d3b83ee07/packages/core/src/session/session.ts#L325-L328)

协调器按 session ID 保存 execution，其中有 owner fiber。中断时标记 `stopping=true`，清除中断前遗留的唤醒请求，记录原因，然后用另一个受管理的 fiber 执行 `Fiber.interrupt(owner)`。[源码](https://github.com/anomalyco/opencode/blob/815de9950b205cd734c9962c4629b07d3b83ee07/packages/core/src/session/run-coordinator.ts#L147-L166)

**中断 API 返回表示请求已接受，清理还可以在执行 fiber 中继续。** 如果此时有新运行请求，`run()` 发现旧 execution 正在 stopping，会先等待其 `done`，再开始新 execution。调用方也可通过 `awaitSettlement` 显式等清理完成。[新运行等待](https://github.com/anomalyco/opencode/blob/815de9950b205cd734c9962c4629b07d3b83ee07/packages/core/src/session/run-coordinator.ts#L124-L134)、[可选等待](https://github.com/anomalyco/opencode/blob/815de9950b205cd734c9962c4629b07d3b83ee07/packages/core/src/session/run-coordinator.ts#L183-L193)

TUI 的 `resume:true` 有特定含义：清理后可继续已经排队的 steering/compaction/move 控制工作，并非自动重跑被中断的模型请求或所有排队提示词。[源码及注释](https://github.com/anomalyco/opencode/blob/815de9950b205cd734c9962c4629b07d3b83ee07/packages/core/src/session/execution.ts#L153-L170)

### 3. 模型与工具如何收尾

Step 在执行 fiber 内消费 `llm.stream(...).pipe(Stream.runForEach(...))`。每个本地工具调用拥有通过 `Effect.forkScoped` 创建的 fiber；它们纳入本次 step 的受管理作用域。[创建工具 fiber](https://github.com/anomalyco/opencode/blob/815de9950b205cd734c9962c4629b07d3b83ee07/packages/core/src/session/runner/step.ts#L103-L138)

模型流消费遭中断后，step 执行 `interruptTools`，内部为 `Fiber.interruptAll`。最终事件发布被 `uninterruptibleMask` 保护，避免清理过程再次在发布一半时停止。[工具集合](https://github.com/anomalyco/opencode/blob/815de9950b205cd734c9962c4629b07d3b83ee07/packages/core/src/session/runner/step.ts#L86-L90)、[中断与等待](https://github.com/anomalyco/opencode/blob/815de9950b205cd734c9962c4629b07d3b83ee07/packages/core/src/session/runner/step.ts#L140-L153)

HTTP 模型传输交给 Effect HttpClient，默认 executor layer 使用 `FetchHttpClient.layer`；这条会话取消主链没有自行创建一个 session 级 `AbortController`。[HTTP 执行](https://github.com/anomalyco/opencode/blob/815de9950b205cd734c9962c4629b07d3b83ee07/packages/ai/src/route/transport/http.ts#L112-L140)、[默认传输](https://github.com/anomalyco/opencode/blob/815de9950b205cd734c9962c4629b07d3b83ee07/packages/ai/src/route/executor.ts#L237-L265)

这里可确定的是“中断本地 Effect 流消费并释放相应资源”。本研究没有检查锁定 Effect 依赖中 FetchHttpClient 的内部实现，因此不把“provider 服务端必定停止生成”当作保证。另一个 WebSocket 会话传输路径在未正常结束时清除 checkpoint 并 poison/close channel。[WebSocket 清理](https://github.com/anomalyco/opencode/blob/815de9950b205cd734c9962c4629b07d3b83ee07/packages/core/src/session/model-transport.ts#L444-L478)

### 4. Shell 有真正的操作系统子进程

Shell 工具等待 job 时安装 `onInterrupt → jobs.cancel`；job 的工作过程安装 `onInterrupt → shell.remove`。[工具清理](https://github.com/anomalyco/opencode/blob/815de9950b205cd734c9962c4629b07d3b83ee07/packages/core/src/tool/plugin/shell.ts#L219-L270)

`Job.cancel` 将状态改成 cancelled 并关闭 job scope。Shell.remove 解除等待并移除命令记录，从而使保存子进程 handle 的 scope 关闭，触发进程释放。[Job 取消](https://github.com/anomalyco/opencode/blob/815de9950b205cd734c9962c4629b07d3b83ee07/packages/core/src/job.ts#L411-L443)、[Shell 删除](https://github.com/anomalyco/opencode/blob/815de9950b205cd734c9962c4629b07d3b83ee07/packages/core/src/shell.ts#L165-L178)、[保持进程作用域](https://github.com/anomalyco/opencode/blob/815de9950b205cd734c9962c4629b07d3b83ee07/packages/core/src/shell.ts#L438-L444)

Shell 创建子进程时配置 `forceKillAfter:3 秒`。跨平台 spawner 在 POSIX 用负 PID 发进程组信号，Windows 使用 `taskkill /pid ... /T /F`；默认先 TERM，超时再 KILL，进程组处理失败时退回单进程终止。[创建配置](https://github.com/anomalyco/opencode/blob/815de9950b205cd734c9962c4629b07d3b83ee07/packages/core/src/shell.ts#L307-L318)、[终止实现](https://github.com/anomalyco/opencode/blob/815de9950b205cd734c9962c4629b07d3b83ee07/packages/util/src/cross-spawn-spawner.ts#L344-L397)、[资源释放](https://github.com/anomalyco/opencode/blob/815de9950b205cd734c9962c4629b07d3b83ee07/packages/util/src/cross-spawn-spawner.ts#L428-L449)

这些是源码中的终止策略；没有在本机实测逃逸子进程、平台权限不足等情况，不能保证任意外部进程必定停止。

### 5. 历史与会话继续

Step 中断后，对尚未结算的工具补 `aborted` 失败结果，并将 assistant 标记为 `Step interrupted`；已经发布的内容保留，由最终事件把未完状态结清。[源码](https://github.com/anomalyco/opencode/blob/815de9950b205cd734c9962c4629b07d3b83ee07/packages/core/src/session/runner/step.ts#L205-L225)

执行级清理完成后发布 `Execution.Interrupted`。用户主动中断会释放持久运行 claim；shutdown 则保留 claim 供重启恢复。投影器记录 idle 时间及 interrupted outcome，故中断并不删除整个会话。[生命周期](https://github.com/anomalyco/opencode/blob/815de9950b205cd734c9962c4629b07d3b83ee07/packages/core/src/session/execution.ts#L118-L145)、[状态投影](https://github.com/anomalyco/opencode/blob/815de9950b205cd734c9962c4629b07d3b83ee07/packages/core/src/session/projector.ts#L411-L442)

### 对 Python 学习项目的启发（设计推论）

最值得借鉴的是把四件事分开：输入按键、请求取消本轮运行、等待工具与历史清理、回到可输入状态。Python 可用独立 `asyncio.Task` 保存“本轮运行”的控制对象；不需要为了支持取消而把模型交互搬进子线程。Effect Fiber 与 asyncio Task 是不同运行时中的异步执行管理机制，不能直接视为同一个 API；Shell 命令则确实需要管理操作系统子进程。

对照时还应注意：OpenCode 是可独立部署的 TUI/服务端结构，本项目目前是直接 `input()` 的 Python CLI，因此无需照搬其 API、持久 inbox 或多层协调器。
