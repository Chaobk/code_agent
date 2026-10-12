# 任务结束信号研究（第一手部分：Pi / Roo Code）

> 本轮复核限定：已通过 Pi 官方 How Pi Works 与 Message Types 页面复核基本循环及消息字段；Roo 中文页面本轮无法访问，保留为原稿资料线索，未独立确认其官方归属或运行时行为。

原稿标注研究日期为 2026-02-06，与其余报告日期不一致；原始采集日期未核实。本轮文档复核日期：2026-10-12。
范围：**只记录我本人在本次会话中实际抓取到的官方页面**。Codex、OpenCode、Cline、Aider、SWE-agent 的源码研究由并行子代理完成，见同目录的 `completion-signal-codex.md`、`completion-signal-opencode.md`、`completion-signal-others.md`。

## 来源限制（重要）

本次会话的 `web_fetch` **无法访问 `github.com`、`raw.githubusercontent.com`、`api.github.com`**，报错为 "hostname resolves to a non-public IP address"。因此本文件不包含任何源码级引用，全部依据**官方文档站**：

- Pi 官方文档站 `pi.dev`（来源仓库标为 `earendil-works/pi`，npm 包 `@earendil-works/pi-coding-agent`）
- Roo Code 中文资料站 `docs.roocode.cn`（本轮未确认其官方归属）（Roo Code 是 Cline 的分支，工具同名，注意不能等同于 Cline 本体）

凡本文件写出的类型名、字段名、事件名，都来自下面列出的、我实际抓取成功的页面。

## 结论

1. **Pi 没有"任务完成工具"。** Pi 的 run 在"没有新的工具结果、也没有排队消息需要再请求一次模型"时自然结束。这是一个纯 ReAct 式的终止条件，与我们项目去掉 `END_TASK` 后的目标形态一致。
2. **Pi 把"为什么结束"记在助手消息的 `stopReason` 上**，取值包含 `stop` / `toolUse` / `length` / `error` / `aborted`(以 `deferred`、`pending` 为流式/延迟态)。这与我们 `ToolStatus`/`finish_reason` 的分工相似：**结束原因是模型响应的属性，不是一个工具**。
3. **Pi 不区分"任务成功"和"任务被阻塞"。** 会话/事件层没有任何 success/blocked 字段；只有 `agent_settled.aborted` 表示"这次 run 是被中止的"。也就是说，"任务是否真的成功"由外部证据（测试、diff）判断，而不是由 Agent 自己申报。
4. **Roo Code 有显式完成工具 `attempt_completion`**，参数是 `result`(必需，总结) 与 `command`(可选，演示结果的 CLI 命令)。它的定位是**面向用户的收尾信号 + UI 呈现 + 反馈检查点**。仅凭该文档不能确定 Roo 的运行循环退出条件，更不能把该描述替代 Cline 不同版本的源码。
5. 两种做法并存，但显式完成工具出现的场合有共性：**需要向用户明确宣告"我完成了"、需要特殊 UI、需要人给反馈**（具体交互还受版本、模式和自动批准配置影响，不能推断每次调用都必须人工确认）。

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

**对 code_agent 的直接参照**：`tool_execution_end` 的字段构成（调用 ID + 工具名 + 结果 + 是否错误 + 耗时）正是我们 M2b 要给 `ToolResultEvent` 定的形状；`agent_settled` 的 `aborted` 布尔值对应我们 `RunFinished.reason` 想表达的"正常结束 vs 被中止"。

### Roo Code：`attempt_completion` 的参数与定位

> "`result` (必需)：最终结果描述，总结了已完成的工作；`command` (可选)：用于演示结果的 CLI 命令"

> "此工具通过展示最终完成工作的摘要来标志任务的结束。它向用户和系统发出信号，表明当前任务已完成。"

关键限定条件（说明它依赖"每次一条工具调用"的对话模型）：

> "不应在确认之前的工具使用成功后再使用（**指南，非强制执行**）"
> "命令在执行前需要用户批准"
> "结果以特殊的 'completion_result' UI 格式出现，不同于普通消息"
> "系统在呈现结果后等待用户反馈……这会创建一个迭代改进周期，而不是结束对话"

来源：[Roo Code — attempt_completion](https://docs.roocode.cn/advanced-usage/available-tools/attempt-completion)（实际抓取成功）

同页还提到解析位于 `parse-assistant-message.ts`、该工具属于 `ALWAYS_AVAILABLE_TOOLS`——但**这些是文档对源码的转述，我没有读到源码本身**，仅作为线索记录。

## 对 code_agent 的推论（属研究推论，不是上述项目的事实）

1. `finish_task` 不是业界的默认做法。Pi 这类"让模型自己决定何时停"的循环，结束条件就是**模型不再请求工具**；我们现在的 `END_TASK` 文本标记反而比两者都弱——既没有 Pi 那样的 `stopReason` 承载结束原因，也没有显式完成工具的可校验参数。
2. 显式完成工具的价值主要在**收尾语义**而非**循环终止**：它把"总结"和"状态"变成有 schema 的参数，可以被校验、可以回灌纠错、可以在 UI 上单独呈现。
3. 因此我们需要的可能不是"结束工具"，而是两件分开的事：
   - **循环终止**：模型一轮没有工具调用 → 正常结束是候选设计；M1 的已提交实现还要求 END_TASK 末行，主目录未提交实现正在移除这一条件；
   - **收尾申报**：一个可选的、无副作用的申报动作，用来表达"完成/阻塞 + 总结"，供 CLI 呈现与退出码使用。
4. Pi 不区分成功与阻塞，说明显式申报不是所有实现共有的机制；本项目是否需要它应根据自身需求判断。若采用 `finish_task`，它是我们对"CLI 需要一个明确收尾状态"的自主选择，应当在文档里写明理由，而不是声称"业界都这么做"。

## 未能确认的部分

- Pi 的源码实现（`packages/agent/src/types.ts` 等）**未直接读取**，github 域名在本次会话不可达。上文的字段名来自官方文档页。
- Roo Code ≠ Cline：`attempt_completion` 在两者中同名且同源，但 Cline 本体最新 schema（是否两个参数都可选、是否有 `task_progress`）未核实。
- Codex / OpenCode / Cline / Aider / SWE-agent 的结论见并行子代理产出的另外三个文件，**不要在本文件引用它们的结论**。
