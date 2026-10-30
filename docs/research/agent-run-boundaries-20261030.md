# Agent 一次运行的边界：pi、OpenCode 与 mini-swe-agent

调查日期：2026-10-30。只读官方源码；未运行上游做持久化实验。pi、mini-swe-agent 使用当日读取的 `main`（可变引用）；OpenCode 固定为 `v2.0.28`，下文不混用旧版的消息 part 设计。

## 结论

行业实现并不是统一规定“每次对话必须向文件写入 `run_start`、`run_end` 两条记录”。首先要区分会话、一次 Agent 循环、一次模型响应和工具执行，其次要区分实时事件与会话存储。pi 有详细的实时生命周期事件，但普通会话 JSONL 并不原样保存这些事件；OpenCode v2.0.28 则有 execution、step、tool 等分层事件，并将有关状态投影到数据库。来源见下文。

| 层次 | pi 的术语 | OpenCode v2.0.28 的术语 | 对当前项目的映射（设计推断） |
| --- | --- | --- | --- |
| 可延续的对话上下文 | session | session | `SessionManager` 管理的会话 |
| 一次 Agent 循环 | agent 生命周期 | execution | 通常对应一次 `Agent.run(prompt)` |
| 一次模型响应及其后续处理 | turn：一个 assistant 响应及工具调用/结果 | step：以 assistantMessageID 关联 | `run` 内的一次模型请求；不一定是整个用户请求 |
| 具体工具执行 | tool_execution 事件 | tool 事件 | 一个 tool_call_id 对应的执行 |

这些只是对应关系，不代表名字或边界完全相同。例如 pi 在同一个循环中还支持 steering、follow-up 输入，因此不能把其 agent 生命周期机械等同为“只包含一条用户消息”。[pi 类型定义](https://github.com/earendil-works/pi/blob/main/packages/agent/src/types.ts#L473)、[pi 循环实现](https://github.com/earendil-works/pi/blob/main/packages/agent/src/agent-loop.ts#L103)、[OpenCode 事件定义](https://github.com/anomalyco/opencode/blob/v2.0.28/packages/schema/src/session-event.ts#L224)

## pi：运行事件丰富，普通 JSONL 主要保存消息与会话状态

`AgentEvent` 区分 `agent_start/end`、`turn_start/end`、`message_start/update/end` 和工具执行事件。源码明确将 turn 定义为一个 assistant 响应及其工具调用/结果。`agent_start` 位于循环外侧，一次 agent 循环可以有多个 turn；`agent_end` 是该次运行最后的事件，监听器处理结束后才真正空闲。[事件类型](https://github.com/earendil-works/pi/blob/main/packages/agent/src/types.ts#L473)、[循环入口](https://github.com/earendil-works/pi/blob/main/packages/agent/src/agent-loop.ts#L103)

但是，`AgentSession._handleAgentEvent` 的常规持久化入口是 `message_end`：普通消息交给 `SessionManager.appendMessage`，custom 消息使用对应记录类型。`turn_end` 还有自定义消息处理职责，不能据此认为它自身必定成为一行会话记录。[事件处理与持久化](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/agent-session.ts#L1097)、[subscribe 接口注释](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/agent-session.ts#L1321)

其内置 `SessionEntry` 包括 message、模型/思考级别变更、压缩、分支摘要、custom 等类型，未包含专门的 `agent_start/end` 或 `turn_start/end` 类型。因此，“pi 发出这些事件”与“pi 的普通 JSONL 原样保存这些事件”是两件事；扩展通过 custom 条目另行记录不在此结论之内。[SessionEntry 定义](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/session-manager.ts#L40)

## OpenCode v2.0.28：execution、step 和 tool 分层，终态有具体含义

execution 有 `Started`、`Succeeded`、`Failed`、`Interrupted`。中断还携带 user、shutdown、superseded、inactivity 等原因。这比单一无内容的“结束标记”表达更多信息。[Execution 定义](https://github.com/anomalyco/opencode/blob/v2.0.28/packages/schema/src/session-event.ts#L224)

step 有 `Started`、`Streamed`、`Ended`、`Failed`，通过 assistantMessageID 关联。结束事件包含 finish、token 用量、费用等数据；`Streamed` 特别表示模型响应体的流结束，与工具是否已经处理完成是不同边界。因此模型停止输出不必然等于整个 execution 结束。[Step 定义](https://github.com/anomalyco/opencode/blob/v2.0.28/packages/schema/src/session-event.ts#L307)

工具也有输入、调用、成功/失败等事件，单独关联 assistant 消息和工具 ID。文本增量及工具进度是 ephemeral，完整文本和工具终态是 durable；工具失败终态可以自带部分结果，不依赖重放全部进度事件。[文本与工具事件定义](https://github.com/anomalyco/opencode/blob/v2.0.28/packages/schema/src/session-event.ts#L366)

这里的 durable 仍不能直接翻译成“所有部署都保存了完整原始事件日志”。Bus 会事务执行数据库 projector；是否额外把原始 durable payload 写进 `EventTable` 由 `persist` 选项控制，该工厂默认是 false。也就是说，业务数据库状态的持久化和完整原始事件日志的保留是两层机制。[Bus 选项与默认值](https://github.com/anomalyco/opencode/blob/v2.0.28/packages/core/src/bus.ts#L159)、[事务及条件写事件表](https://github.com/anomalyco/opencode/blob/v2.0.28/packages/core/src/bus.ts#L379)

例如 execution 终态有 projector 更新 session 的 `time_idle`、`idle_outcome`；shutdown 中断有专门例外。公开 session schema 将 outcome 定义为最近一次已完成 execution 的结果，取 succeeded、failed、interrupted。这个会话级摘要也不等于每次 execution 的完整历史。[终态 projector](https://github.com/anomalyco/opencode/blob/v2.0.28/packages/core/src/session/projector.ts#L396)、[Session.Info](https://github.com/anomalyco/opencode/blob/v2.0.28/packages/schema/src/session.ts#L26)

## mini-swe-agent：消息快照加退出状态

`DefaultAgent.run()` 开始时重置消息，写入系统消息和任务输入，随后循环执行 `step()`；每步在 `finally` 中保存完整 JSON 快照。结束通过 `role="exit"` 消息表达，其 `extra.exit_status` 还会汇总到保存结果的 `info.exit_status`，并不依赖名为 `run_start/run_end` 的一对 JSONL 条目。[run 与退出消息](https://github.com/SWE-agent/mini-swe-agent/blob/main/src/minisweagent/agents/default.py#L67)、[serialize 与 save](https://github.com/SWE-agent/mini-swe-agent/blob/main/src/minisweagent/agents/default.py#L147)

这说明结束原因可以放在独立元数据或退出记录中。不过该默认实现以单次任务轨迹为单位，不能直接当作当前项目同一会话内多次 `Agent.run()` 的存储方案。更多保存与异常处理细节见[已有流程调研](mini-swe-agent-control-flow-20261030.md)。

## 对本项目术语的启示

这里的“一次运行”宜先指明：一条用户输入触发后，Agent 可能调用模型、执行工具、再次调用模型，直至返回、失败或中断；同一个会话可以有多次这样的运行。它更接近 pi 的 agent 生命周期及 OpenCode 的 execution，而不是 pi 的单个 turn。

是否增加边界记录应由需求决定。只需恢复消息上下文时，pi 已说明“保存消息”是一条成立的路径；若要判断某次用户请求是否完成、为何结束、哪些消息属于同一次请求，就需要额外的关联和终态语义。`run_start` / `run_end` 可以是实现这些语义的一种简单形式，两者的名字和数量本身不是行业统一规范。此段是基于以上实现的设计推断，并非建议现在照搬完整事件系统。

当前项目可先沿用已有消息与工具调用 ID。如果只读查看还需要显示一次请求的结束原因，再补最小的运行终态：关联哪次请求、结束状态，必要时附错误与时间。顺序执行下，用户消息也可承担开始边界；若选择独立 start/end 记录，同一次 run 的两端应能关联，end 应携带具体状态。没有终态记录仅能说明“未记录结束”，不能证明进程一定崩溃，因为仍可能正在运行或结束记录保存失败。运行记录属于轨迹数据，不必作为普通对话消息送给模型。以上均为本项目设计建议，本次未修改源码或计划。
