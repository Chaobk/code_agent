# 任务结束信号资料：Pi 文档与 Roo Code 线索

> 本轮复核限定：已通过 Pi 官方 How Pi Works 与 Message Types 页面复核基本循环及消息字段；Roo 中文页面本轮无法访问，保留为原稿资料线索，未独立确认其官方归属或运行时行为。

原稿标注研究日期为 2026-02-06，与其余报告日期不一致；原始采集日期未核实。本轮文档复核日期：2026-10-12。
范围：保留原核查读取的 Pi 官方文档，以及官方归属未确认的 Roo 中文资料线索。Codex、OpenCode、Cline、Aider、SWE-agent 的源码研究由并行子代理完成，见同目录的 `completion-signal-codex.md`、`completion-signal-opencode.md`、`completion-signal-others.md`。

## 来源限制（重要）

本次会话的 `web_fetch` **无法访问 `github.com`、`raw.githubusercontent.com`、`api.github.com`**，报错为 "hostname resolves to a non-public IP address"。因此本文件不包含任何源码级引用，来源分为官方文档和归属未确认的资料站：

- Pi 官方文档站 `pi.dev`（来源仓库标为 `earendil-works/pi`，npm 包 `@earendil-works/pi-coding-agent`）
- Roo Code 中文资料站 `docs.roocode.cn`（本轮未确认其官方归属）（Roo Code 是 Cline 的分支，工具同名，注意不能等同于 Cline 本体）

下面的字段摘录仅代表原核查页面，不能据此穷举整个系统。Pi 后续固定源码核查见 [Pi 设计研究](pi-coding-agent-design.md)；Roo 资料仍保留待核验状态。

## 结论与证据范围

Pi 文档描述模型响应、工具结果和队列驱动的循环；会话层还可能处理重试、压缩和续跑，因此 agent_end 与 agent_settled 含义不同。它们都不能证明用户目标已经达成。模型停止原因、底层运行结束与外部测试结果分别判断。

没有在所读事件表看到 success/blocked 不能证明 Pi 所有扩展和运行路径都无法表达这些状态。默认完成工具、扩展工具和任务成功判定也不能混为一谈。

Roo 中文资料提到 attempt_completion 的呈现与反馈用途，但官方归属和对应版本未独立确认。以下仅保留资料线索，不用它证明 Roo 或 Cline 当前运行时的字段、独占规则或审批行为。

## 证据

### Pi：run 结束条件

> "The provider streams an assistant response, which can contain text and tool calls. Pi records the response, executes each tool call, and records the results. That completes one turn. **If tool results or queued messages require another model request, Pi starts another turn. Otherwise, the run ends.**"

来源：[How Pi Works](https://pi.dev/docs/latest/how-pi-works)（实际抓取成功）

### Pi：结束原因在助手消息上，不在工具上

`AssistantMessage` 的字段（节选）：

```
stopReason: "pending" | "stop" | "length" | "toolUse" | "error" | "aborted" | "deferred";
deferred?: DeferredHandle;
errorMessage?: string;
rawStopReason?: string;
endTurn?: boolean;
```

来源：[Message Types](https://pi.dev/docs/latest/message-types)（实际抓取成功）

会话文件里持久化的助手消息同样带 `"stopReason":"stop"`，工具结果消息带 `toolCallId` / `toolName` / `content` / `isError`，没有额外的"任务完成条目"：

```
{"type":"message","id":"b2c3d4e5","parentId":"a1b2c3d4",...,"message":{"role":"assistant","content":[{"type":"text","text":"Hi!"}],...,"stopReason":"stop",...}}
{"type":"message","id":"c3d4e5f6","parentId":"b2c3d4e5",...,"message":{"role":"toolResult","toolCallId":"call_123","toolName":"bash","content":[{"type":"text","text":"output"}],"isError":false,...}}
```

来源：[Session File Format](https://pi.dev/docs/latest/session-format)（实际抓取成功）

### Pi：事件层只有"run 结束"，没有"任务完成"

JSON 事件流的标准序列（节选）：

```
{"type":"turn_end","message":{"role":"assistant",...},"toolResults":[]}
{"type":"agent_end","messages":[...],"willRetry":false}
{"type":"agent_settled","aborted":false}
```

> "`agent_end` closes one low-level agent run. Automatic retry, overflow recovery, compaction retry, steering, or follow-up work can still continue. `agent_settled` means Pi has no remaining automatic work for that session-level run."

事件表里没有 `task_complete` / `finished` 之类记录，只有 `tool_execution_start` / `tool_execution_update` / `tool_execution_end`（后者带 `toolCallId`、`toolName`、`result`、`isError`、`durationMs`）。

来源：[JSON Event Stream](https://pi.dev/docs/latest/json)（实际抓取成功）

对本项目的参照是结果关联和生命周期边界，不能逐字段照搬。当前 ToolResultEvent 携带调用关联与 ToolResult；运行结束通过 RunResult 和异常表达，RunFinished 已删除，耗时也不是当前事件的必需字段。

### Roo Code：`attempt_completion` 的参数与定位

> "`result` (必需)：最终结果描述，总结了已完成的工作；`command` (可选)：用于演示结果的 CLI 命令"

> "此工具通过展示最终完成工作的摘要来标志任务的结束。它向用户和系统发出信号，表明当前任务已完成。"

该资料还记录了以下交互描述；这些描述不足以证明每消息单工具是运行时不变量：

> "不应在确认之前的工具使用成功后再使用（**指南，非强制执行**）"
> "命令在执行前需要用户批准"
> "结果以特殊的 'completion_result' UI 格式出现，不同于普通消息"
> "系统在呈现结果后等待用户反馈……这会创建一个迭代改进周期，而不是结束对话"

来源：[Roo Code — attempt_completion](https://docs.roocode.cn/advanced-usage/available-tools/attempt-completion)（原稿记录曾取得页面；后续复核未成功，官方归属未确认）

同页还提到解析位于 `parse-assistant-message.ts`、该工具属于 `ALWAYS_AVAILABLE_TOOLS`——但**这些是文档对源码的转述，我没有读到源码本身**，仅作为线索记录。

## 对 code_agent 的推论

本项目已经采用正常 stop 且无工具调用结束，END_TASK 是普通文本；有调用先回灌观察。finish_task 属于按需增强，不能从少数案例推出行业统一要求。

如未来增加完成/阻塞申报，schema 只能校验格式，任务是否完成仍需外部证据。回放、恢复和计划执行可以基于现有运行结果分别设计，不以完成工具为先决条件。[现行计划](../../PLAN.md)

## 未能确认的部分

- Pi 的源码实现（`packages/agent/src/types.ts` 等）**未直接读取**，github 域名在本次会话不可达。上文的字段名来自官方文档页。
- Roo Code ≠ Cline：`attempt_completion` 在两者中同名且同源，但 Cline 本体最新 schema（是否两个参数都可选、是否有 `task_progress`）未核实。
- Codex / OpenCode / Cline / Aider / SWE-agent 的结论见并行子代理产出的另外三个文件，**不要在本文件引用它们的结论**。
