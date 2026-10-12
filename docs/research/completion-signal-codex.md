# Codex 的任务结束信号研究

> 本轮复核限定：源码/搜索经第三方代理或 CDN 取得；多通道一致性不是官方独立认证。未读取全部工具注册与搜索命中，不将关键词零命中推断为全仓库不存在任何完成机制。run_turn 内未见步数检查不等于系统全局没有运行限制。

研究日期：2026-10-12。

范围与来源限制说明：本文只覆盖 OpenAI Codex 的官方 Rust 实现（`openai/codex` 仓库），不涉及 TypeScript CLI 或桌面端。**抓取方式需要特别说明**：本机 DNS 把 `github.com`、`raw.githubusercontent.com`、`api.github.com` 全部解析到 `127.0.0.1`，因此这些域名的源码页面**无法抓取**。实际读取的是 jsDelivr 对同一仓库的**第三方镜像提供的仓库源码**：`https://fastly.jsdelivr.net/gh/openai/codex@<分支或tag>/<路径>`（HTTP 200，返回的是仓库原始文件内容，不是二次转述、不是搜索摘要）。

据此读到了两个 ref（固定 tag `rust-v0.63.0` 与 `main` 分支）下共 15 个源码文件，全部作为主要依据：`rust-v0.63.0` 标签的 `core/src/codex.rs`、`protocol/src/protocol.rs`、`protocol/src/models.rs`、`core/src/response_processing.rs`、`core/src/tasks/mod.rs`、`core/src/tasks/regular.rs`；以及 `main` 分支的 `core/src/tasks/regular.rs`、`core/src/tasks/mod.rs`、`core/src/session/turn.rs`、`core/src/stream_events_utils.rs`、`protocol/src/models.rs`、`protocol/src/protocol.rs`、`tools/src/lib.rs`、`tools/src/tool_spec.rs`、`core/src/tools/spec_plan.rs`。**未抓到**：GitHub 的 issue 页面、任何 commit 页面、以及行号锚点链接。

行号是**真实源码行号**：`web_fetch` 的长返回会溢出到本机临时文件并保留完整原文，本文的行号是在该完整原文上定位得到的，不是估算。镜像站点内容可能随上游变化；除特别标注外，行号对应当日抓到的快照。

**第二通道（经 gh-proxy 代理读取，用于交叉验证）**：本文成稿后又通过 `https://gh-proxy.com/https://api.github.com/...` 与 `https://gh-proxy.com/https://raw.githubusercontent.com/...` 直连到了 GitHub 本体。经代理读到的 `main` 分支 `codex-rs/protocol/src/protocol.rs` 原文与镜像版**逐字一致**（含 `TurnCompleteEvent` 的 8 个字段与 `#[serde(rename = "task_complete", alias = "turn_complete")]`），因此两通道内容一致；它们均经第三方传输，不构成官方独立认证。GitHub 代码搜索 API 返回的命中文件位于提交 `a9b0815167c27ab13719dd12d13b63e02b70d221`（2026-10-12 抓取时）；仓库元数据确认 `default_branch = "main"`。[仓库元数据](https://gh-proxy.com/https://api.github.com/repos/openai/codex)

---

## 结论

Codex 判断"这一轮结束"用的是 **(a) 模型不再发起工具调用**，而不是任何专门的结束工具。它的判定点非常明确：模型流式返回的每个 item 都过一遍 `handle_output_item_done`，只要其中出现一个工具调用就把 `needs_follow_up = true`；一轮流结束后若 `needs_follow_up` 为假，就认为模型已经把话说完、被要求做的事做完了，退出循环（`session/turn.rs` L661 `if !needs_follow_up {` → L762 `break;`）。**Codex 里不存在 `finish_task` / `task_complete` 工具，也不存在任何"我完成了"的工具调用**——`ToolSpec` 枚举只有 `Function`、`Namespace`、`ToolSearch`、`WebSearch`、`Freeform` 五种形态，`ResponseItem` 枚举里也没有代表"结束"的变体，GitHub 官方代码搜索对 `finish_task` 的命中数为 **0**。

更精确地说，退出条件是"**模型没有工具调用，且没有待处理的用户输入**"：`let needs_follow_up = model_needs_follow_up || has_pending_input;`（L578）。所以在 Codex 里"说完了"并不足够——如果用户在这轮运行期间又插了话，循环会带着新输入再跑一轮而不是结束。

"结束"这个信号在 Codex 里由**协议事件**承载，而不是由工具调用承载：任务收尾统一发生在一处（`spawn_task`/`start_task` 的完成后处理），由 `Session::on_task_finished` 发出 `EventMsg::TurnComplete(TurnCompleteEvent)`。**注意一个极易混淆的点**：这个事件的 JSON 线上类型名是 `"task_complete"`（serde 重命名，`"turn_complete"` 是 v2 别名）——所以 `task_complete` 是**事件名**，不是工具名。

成功与被中断/失败是**被区分**的，但不是靠一个"成功/失败"布尔字段，而是靠**两个不同的事件**加字段组合：正常收尾发 `TurnComplete`；被中断走 `EventMsg::TurnAborted(TurnAbortedEvent)`（携带 `reason: TurnAbortReason`）。此外 `TurnCompleteEvent` 带一个可选的 `error: Option<ErrorEvent>`，文档注释写明它是"本轮未成功完成时的终止错误详情"，而失败态还会在 `ThreadIdleCause` 上体现为 `Failed`。

关于"结束调用不能与其他工具调用混在同一轮"：这个问题在 Codex 里**不成立**，因为它根本没有结束调用。不存在这种约束，也就没有对应的校验代码——这一点本文如实标注为"设计上不存在"，而非"未查到"。另外，`run_turn` 的 6 个循环出口中**没有一个以步数为条件**，Codex 不靠"最大步数"来终结一轮。

---

## 证据

### 1. 结束判定 = 模型不再发起工具调用（主要证据）

`main` 分支把旧的 `core/src/codex.rs` 拆分成了 `core/src/session/turn.rs`（循环）与 `core/src/stream_events_utils.rs`（单条 item 的判定）。判定逻辑如下。

第一处，`needs_follow_up` 的唯一"置真"路径就是"模型发起了工具调用"（[stream_events_utils.rs](https://fastly.jsdelivr.net/gh/openai/codex@main/codex-rs/core/src/stream_events_utils.rs)，`OutputItemResult` 定义与 `handle_output_item_done` 的匹配分支位于该文件中段，约 L185-L240）：

```rust
#[derive(Default)]
pub(crate) struct OutputItemResult {
    pub last_agent_message: Option<String>,
    pub needs_follow_up: bool,
    pub tool_future: Option<InFlightFuture<'static>>,
}
```

```rust
match ToolRouter::build_tool_call(item.clone()) {
    // The model emitted a tool call; ...
    Ok(Some(call)) => {
        ...
        output.needs_follow_up = true;
        output.tool_future = Some(tool_future);
    }
    // No tool call: convert messages/reasoning into turn items and mark them as complete.
    Ok(None) => {
        ...
        output.last_agent_message = finalized_facts.and_then(|facts| facts.last_agent_message);
    }
```

第二处，回合循环据此决定是继续还是收尾（[session/turn.rs](https://fastly.jsdelivr.net/gh/openai/codex@main/codex-rs/core/src/session/turn.rs)）：

- `pub(crate) async fn run_turn(` 在 L168，循环 `loop {` 在 L429；
- L552 `needs_follow_up: model_needs_follow_up,`（从采样结果取出）；
- L578 `let needs_follow_up = model_needs_follow_up || has_pending_input;`
- L661 `if !needs_follow_up {` ——进入正常收尾分支；
- L709 `if stop_outcome.should_stop { break; }`（Stop hook 要求停止时的提前退出）。

同时，L1875-L1878 定义了承载该判定的结构：

```rust
struct SamplingRequestResult {
    needs_follow_up: bool,
    last_agent_message: Option<String>,
}
```

`run_turn` 的文档注释（[session/turn.rs](https://fastly.jsdelivr.net/gh/openai/codex@main/codex-rs/core/src/session/turn.rs) L99-L109）把语义写得很直白：

> - If the model requests a function call, we execute it and send the output back to the model in the next sampling request.
> - If the model sends only an assistant message, we record it in the conversation history and we consider the turn complete.

#### 1.1 循环的退出条件原文（经 gh-proxy 代理读取）

以下为 `codex-rs/core/src/session/turn.rs` 中 `run_turn` 的循环收尾段**原文**（L760-L765，取自经 gh-proxy 代理抓到的 raw 文件：[gh-proxy raw turn.rs](https://gh-proxy.com/https://raw.githubusercontent.com/openai/codex/main/codex-rs/core/src/session/turn.rs)）：

```rust
                        warn!(error = %err, "Post-turn compaction failed; preserving the completed turn");
                    }
                    break;
                }
                continue;
            }
```

这段 `break` 位于 L661 的 `if !needs_follow_up {` 分支**末尾**，即"模型本轮没有工具调用、且没有待处理用户输入"时才对循环说停；随后的 `}` 关闭该分支，之后的 `continue` 则服务于"仍需跟进"（`needs_follow_up == true`）的情形，把控制权交回 L429 的 `loop {` 再发起下一次采样请求。

关键点：**这确实是"没有 tool call 就结束"的结构，但不是写成 `if !has_tool_calls { complete }`，而是 `needs_follow_up = model_needs_follow_up || has_pending_input`，然后 `if !needs_follow_up { ... break; }`。** `model_needs_follow_up` 即"本轮模型发起了工具调用"，`has_pending_input` 是"用户在本轮运行期间又插了话"。所以结束条件其实是"模型没有工具调用 **且** 没有待处理输入"。

#### 1.2 `run_turn` 循环的全部退出路径（经 gh-proxy 代理读取）

`run_turn` 的循环体只有 6 个出口，全部为 `break` 或 `return`，逐一列出（行号来自上引 gh-proxy raw 文件）：

| 行号 | 形式 | 触发条件 | 之后走哪条事件路径 |
| --- | --- | --- | --- |
| L451 | `break;` | 循环开头 `run_hooks_and_record_inputs(...)` 返回真（steered user input 的 hook 直接终止本轮） | 正常收尾 → 上层 `Ok(...)` → `TurnComplete` |
| L709 | `break;` | Stop hook 要求停止（`stop_outcome.should_stop`） | 正常收尾 → `TurnComplete` |
| **L762** | **`break;`** | **`!needs_follow_up`：模型无工具调用且无待处理输入（主路径）** | **正常收尾 → `TurnComplete`** |
| L820 | `break;` | `CodexErrorDetails::InvalidImageRequest()`，先发 `EventMsg::Error` | 正常收尾但带错误 → `TurnComplete { error: Some(..) }` |
| L837 | `break;` | 其他 `Err(e)`：发 `EventMsg::Error`，注释写着 "let the user continue the conversation" | 正常收尾但带错误 → `TurnComplete { error: Some(..) }` |
| L719 | `return Ok(None);` | legacy after-agent hook 返回真 | 正常收尾 → `TurnComplete`（`last_agent_message` 为 `None`） |

"继续循环"的两个出口是 L658 与 L764 的 `continue`。其中 L613-L625 的 **token 超限滚动**属于 `continue` 而非退出：

```rust
let should_roll_over = needs_follow_up
    && (sess.take_new_context_window_request().await || token_limit_reached);
...
if should_roll_over {
    if let Err(err) = run_auto_compact(...).await { ... }
    can_drain_pending_input = !model_needs_follow_up || ...;
    continue;
}
```

这印证了代码里那句注释："as long as compaction works well in getting us way below the token limit, we shouldn't worry about being in an infinite loop."（L624）

#### 1.3 三种非正常终止如何与 `TurnComplete` 区分

- **被 Interrupt**：`TurnAborted` 事件，**不是** `TurnComplete`。机制是 `Session::abort_all_tasks` / `finish_turn_abort` 取消 `CancellationToken` 并调用 `handle_task_abort`，最终发 `EventMsg::TurnAborted(TurnAbortedEvent { ..., reason, error, ... })`（[tasks/mod.rs](https://gh-proxy.com/https://raw.githubusercontent.com/openai/codex/main/codex-rs/core/src/tasks/mod.rs)）。`Op::Interrupt` 的文档注释亦写明 "This server sends [`EventMsg::TurnAborted`] in response"。此外 `on_task_finished` 中 `Err(err) if matches!(err.details(), CodexErrorDetails::TurnAborted) => (None, Some(TurnAbortReason::Interrupted))` 会把 `run_turn` 冒泡上来的 abort 错误转成 `TurnAborted` 事件。
- **出错**：没有单独的事件类型，而是"仍然收尾 + 在 `TurnComplete.error` 里带 `ErrorEvent`"。`on_task_finished` 对非 abort 错误先发 `EventMsg::Error` 再发 `TurnComplete`（`error` 为 `Some`）。
- **步数上限**：**未发现** `run_turn` 循环内有以步数/迭代次数为条件的 `break`。三个 `break`（L451/L709/L762）与两个错误 `break` 都与步数无关；token 超限走的是 `continue` + 压缩。因此本次抓到的源码范围内**没有"最大步数"这一退出条件**——如果存在，应在配置层或 `step_context`/`context_window` 中，本文未追查到（见"未能确认"）。


### 2. 旧版本（`rust-v0.63.0`）用同一思路，但写法更直白

锁定 tag 的版本里，循环在 `core/src/codex.rs` 的 `run_task`（L1836 起，`loop {` 在 L1861）。判定条件不是布尔量，而是"**要不要回传给模型的工具结果列表是否为空**"（L1925-L1938）：

```rust
if responses.is_empty() {
    last_agent_message = get_last_assistant_message_from_turn(
        &items_to_record_in_conversation_history,
    );
    sess.notifier()
        .notify(&UserNotification::AgentTurnComplete { ... });
    break;
}
continue;
```

而 `responses` 只在发现工具调用时才有元素——`process_items` 里"拿不到 response 的 item（纯助手消息）走 `None => {}`，不进 `responses`"（[response_processing.rs](https://fastly.jsdelivr.net/gh/openai/codex@rust-v0.63.0/codex-rs/core/src/response_processing.rs) L20-L26、L67）：

```rust
if let Some(response) = &response {
    responses.push(response.clone());
}
...
None => {}
```

`run_task` 的文档注释同样如此（[codex.rs](https://fastly.jsdelivr.net/gh/openai/codex@rust-v0.63.0/codex-rs/core/src/codex.rs) L1833-L1834）：

> - If the model sends only an assistant message, we record it in the conversation history and we consider the task complete.

### 3. 没有专门的结束工具

**(a) 工具 schema 的顶层类型里没有结束形态。** `ToolSpec` 的完整变体只有五个（[tools/src/tool_spec.rs](https://fastly.jsdelivr.net/gh/openai/codex@main/codex-rs/tools/src/tool_spec.rs) L21-L70）：

```rust
pub enum ToolSpec {
    #[serde(rename = "function")]
    Function(ResponsesApiTool),
    #[serde(rename = "namespace")]
    Namespace(ResponsesApiNamespace),
    #[serde(rename = "tool_search")]
    ToolSearch { ... },
    #[serde(rename = "web_search")]
    WebSearch { ... },
    #[serde(rename = "custom")]
    Freeform(FreeformTool),
}
```

**(b) 模型输出 item 的类型里没有"结束"变体。** `main` 分支 `ResponseItem` 从 L1031 起，可见变体为 `AdditionalTools`、`Message`、`AgentMessage`、`Reasoning`、`LocalShellCall`、`FunctionCall`(L1102)、`ToolSearchCall`(L1128)、`ToolSearchOutput`(L1210)、`…`、`Other`(L1301)；`rust-v0.63.0` 的 `ResponseItem` 变体为 `Message`、`Reasoning`、`LocalShellCall`、`FunctionCall`、`FunctionCallOutput`、`CustomToolCall`、`CustomToolCallOutput`、`WebSearchCall`、`GhostSnapshot`、`CompactionSummary`、`Other`（[protocol/src/models.rs](https://fastly.jsdelivr.net/gh/openai/codex@main/codex-rs/protocol/src/models.rs)、[rust-v0.63.0 版本](https://fastly.jsdelivr.net/gh/openai/codex@rust-v0.63.0/codex-rs/protocol/src/models.rs)）。两版的完整变体列表中**都没有** `FinishTask` / `TaskComplete` / `Done` / `Submit` 之类的东西。

**(c) 注册的内置工具里没有结束工具。** `add_core_tool_sources` 汇总四类来源：`add_shell_tools`、`add_mcp_resource_tools`、`add_core_utility_tools`、`add_collaboration_tools`（[core/src/tools/spec_plan.rs](https://fastly.jsdelivr.net/gh/openai/codex@main/codex-rs/core/src/tools/spec_plan.rs) L1199-L1216）。逐一读这些注册点，注册的是 `ExecCommandHandler` / `WriteStdinHandler`、`ListMcpResourcesHandler` 等、`PlanHandler`、`WaitForEnvironmentHandler`、`RequestUserInputHandler`、`NewContextWindowHandler`、`GetContextRemainingHandler`、`CurrentTimeHandler`、`SleepHandler`、`ApplyPatchHandler`、`ViewImageHandler`、`SpawnAgentHandler`、`SendInputHandler`、`WaitAgentHandler`、`CloseAgentHandler`（`main` 新增的 v2 协作工具还有 `FollowupTaskHandlerV2` L1546、`InterruptAgentHandler`、`ListAgentsHandlerV2`）等。**没有任何一个承担"宣告任务完成"。** 需要特别提醒：`FollowupTask` 这个名字看起来像"结束/交付任务"，但它是多 Agent 协作里给子 agent 追加任务的工具，与"结束本轮"无关。

**(d) 对已抓到的全部 Codex 源码文本做本地全文扫描**（`finish_task|task_complete|FinishTask|SubmitTask|fn finish|done_tool`）：唯一命中是协议事件上的 `#[serde(rename = "task_complete", alias = "turn_complete")]`，见下一节。这从反面支持"不存在结束工具"。

**(e) GitHub 官方代码搜索（经 gh-proxy 代理调用）的命中计数**，作为"不存在完成工具"的可复核证据。所有查询均针对整个 `openai/codex` 仓库，返回 `total_count`：

| 查询词 | `total_count` | 说明 |
| --- | --- | --- |
| `finish_task` | **0** | 无任何命中 |
| `finish_task in:file` | **0** | 换限定词后仍为 0 |
| `EndTurn` / `SubmitTask` / `PubSubTask` | **0** | 无任何命中 |
| `has_tool_calls` / `no_tool_calls` | **0** | Codex 不用这类命名，实际用的是 `needs_follow_up` |
| `TaskCompleteEvent` | **0** | 该类型在 `main` 上已不存在（仅 v0.63.0 等旧版有） |
| `TurnComplete` | 366 | 命中集中在协议、TUI、测试，属**事件**而非工具 |
| `TurnAbortReason`（`enum` 声明） | 2 | `codex-rs/protocol/src/protocol.rs`（定义）与 `codex-rs/tui/src/chatwidget.rs`；**确认该枚举定义仍在 `protocol.rs`，不在 `legacy_events.rs`/`items.rs`** |

查询示例（可自行复核）：[finish_task](https://gh-proxy.com/https://api.github.com/search/code?q=repo%3Aopenai%2Fcodex+finish_task+in%3Afile)、[TaskCompleteEvent](https://gh-proxy.com/https://api.github.com/search/code?q=repo%3Aopenai%2Fcodex+%22TaskCompleteEvent%22)、[enum TurnAbortReason](https://gh-proxy.com/https://api.github.com/search/code?q=repo%3Aopenai%2Fcodex+%22enum+TurnAbortReason%22)。

### 4. "结束"信号的真实承载：`TurnComplete` 事件（线上名 `task_complete`）

收尾统一发生在任务完成处，而不是各个任务自己发（[core/src/tasks/mod.rs](https://fastly.jsdelivr.net/gh/openai/codex@main/codex-rs/core/src/tasks/mod.rs)，`on_task_finished`）。其入参就是上一节的判定结果 `SessionTaskResult = CodexResult<Option<String>>`，其中 `Ok(Some(msg))` 即"模型自然说完话"：

```rust
let (last_agent_message, abort_reason) = match task_result {
    Ok(last_agent_message) => (last_agent_message, None),
    Err(err) if matches!(err.details(), CodexErrorDetails::TurnAborted) => {
        (None, Some(TurnAbortReason::Interrupted))
    }
    Err(err) => { /* 发 EventMsg::Error，再按未中断收尾 */ (None, None) }
};
```

随后按 `abort_reason` 分叉成两种终止事件：

```rust
let event = if let Some(reason) = abort_reason {
    ...
    EventMsg::TurnAborted(TurnAbortedEvent { ..., reason, error: None, ... })
} else {
    let error = turn_context.terminal_error.lock().await.clone();
    ...
    EventMsg::TurnComplete(TurnCompleteEvent {
        root_turn_id: ..., turn_id: ..., last_agent_message,
        error, started_at, completed_at, duration_ms, time_to_first_token_ms,
    })
};
```

`TurnCompleteEvent` 的**确切字段**（[protocol/src/protocol.rs](https://fastly.jsdelivr.net/gh/openai/codex@main/codex-rs/protocol/src/protocol.rs)）：

```rust
pub struct TurnCompleteEvent {
    pub root_turn_id: Option<String>,
    pub turn_id: String,
    pub last_agent_message: Option<String>,
    /// Terminal error details when the turn completed unsuccessfully.
    pub error: Option<ErrorEvent>,
    pub started_at: Option<i64>,
    pub completed_at: Option<i64>,
    pub duration_ms: Option<i64>,
    pub time_to_first_token_ms: Option<i64>,
}
```

枚举变体本身带 serde 重命名，**这是 `task_complete` 这个字符串在 Codex 里的唯一来源**（[protocol/src/protocol.rs](https://fastly.jsdelivr.net/gh/openai/codex@main/codex-rs/protocol/src/protocol.rs)，约 L1516-L1528）：

```rust
/// Agent has started a turn.
/// v1 wire format uses `task_started`; accept `turn_started` for v2 interop.
#[serde(rename = "task_started", alias = "turn_started")]
TurnStarted(TurnStartedEvent),

/// Agent has completed all actions.
/// v1 wire format uses `task_complete`; accept `turn_complete` for v2 interop.
#[serde(rename = "task_complete", alias = "turn_complete")]
TurnComplete(TurnCompleteEvent),
```

在 `rust-v0.63.0` 里这个事件还叫 `TaskComplete`，payload 只有一个字段（[protocol/src/protocol.rs](https://fastly.jsdelivr.net/gh/openai/codex@rust-v0.63.0/codex-rs/protocol/src/protocol.rs) L453、L731-L739）：

```rust
/// Agent has completed all actions
TaskComplete(TaskCompleteEvent),
...
pub struct TaskCompleteEvent {
    pub last_agent_message: Option<String>,
}

pub struct TaskStartedEvent {
    pub model_context_window: Option<i64>,
}
```

同一 tag 里收尾发事件的位置（[core/src/tasks/mod.rs](https://fastly.jsdelivr.net/gh/openai/codex@rust-v0.63.0/codex-rs/core/src/tasks/mod.rs)）：

```rust
pub async fn on_task_finished(
    self: &Arc<Self>,
    turn_context: Arc<TurnContext>,
    last_agent_message: Option<String>,
) {
    ...
    let event = EventMsg::TaskComplete(TaskCompleteEvent { last_agent_message });
    self.send_event(turn_context.as_ref(), event).await;
}
```

`main` 分支的 `RegularTask::run` 已经不再自己发完成事件，只把 `last_agent_message` 返回给上层，`TurnStarted` 则改成由任务内联发出（[core/src/tasks/regular.rs](https://fastly.jsdelivr.net/gh/openai/codex@main/codex-rs/core/src/tasks/regular.rs)）：

```rust
let last_agent_message = run_turn(...).await?;
// Terminal errors are already reported. ...
if ctx.terminal_error.lock().await.is_some() {
    return Ok(last_agent_message);
}
if !sess.input_queue.has_pending_input(&sess.active_turn).await {
    return Ok(last_agent_message);
}
next_input = Vec::new();
```

补充一个**当前 main 分支新增的暂停条件**：上一段返回 `Ok(...)` 的路径要求"没有待处理输入"；若用户在模型运行期间又发了消息（`has_pending_input`），任务会带着新输入再跑一轮 `run_turn` 而不结束。也就是说"结束"还额外受**输入队列是否为空**约束。

### 5. 成功 vs 被阻塞/放弃：靠两个事件 + `reason`/`error` 字段区分

- **正常完成**：`EventMsg::TurnComplete(TurnCompleteEvent)`，其 `error` 为 `None`。
- **被中断/放弃**：`EventMsg::TurnAborted(TurnAbortedEvent)`，携带 `reason: TurnAbortReason`。`rust-v0.63.0` 的取值为 `Interrupted`、`Replaced`、`ReviewEnded`（[protocol/src/protocol.rs](https://fastly.jsdelivr.net/gh/openai/codex@rust-v0.63.0/codex-rs/protocol/src/protocol.rs)）：

```rust
pub struct TurnAbortedEvent {
    pub reason: TurnAbortReason,
}

pub enum TurnAbortReason {
    Interrupted,
    Replaced,
    ReviewEnded,
}
```

`main` 分支的 `TurnAbortedEvent` 已扩展出 `root_turn_id`、`turn_id`、`error`、`started_at`、`completed_at`、`duration_ms` 字段，并且判定中出现 `TurnAbortReason::BudgetLimited`（[core/src/tasks/mod.rs](https://fastly.jsdelivr.net/gh/openai/codex@main/codex-rs/core/src/tasks/mod.rs)）。
- **本轮失败但仍算收尾**：`TurnCompleteEvent.error` 为 `Some(ErrorEvent)`，`ErrorEvent` 里用 `codex_error_info: Option<CodexErrorInfo>` 分类；同一分支还维护了空闲原因（[core/src/tasks/mod.rs](https://fastly.jsdelivr.net/gh/openai/codex@main/codex-rs/core/src/tasks/mod.rs)）：

```rust
let idle_cause = if matches!(
    abort_reason.as_ref(),
    Some(TurnAbortReason::Interrupted | TurnAbortReason::BudgetLimited)
) {
    ThreadIdleCause::Interrupted
} else if abort_reason.is_none() && turn_context.terminal_error.lock().await.is_some() {
    ThreadIdleCause::Failed
} else {
    ThreadIdleCause::Completed
};
```

关键区别在于：**任务返回 `Err` 并不会让"结束信号"消失**。`on_task_finished` 对非 `TurnAborted` 的错误会先发 `EventMsg::Error`，然后仍然发 `TurnComplete`（`error` 字段带上终止错误），所以"结束"与"成功"在协议层是两个正交维度。

`rust-v0.63.0` 的对应区分更弱，只有 `TaskComplete` 和 `TurnAborted` 两个事件，没有 `error` 字段——失败信息走独立的 `EventMsg::Error`。

### 6. 关于"结束调用能否与其他工具调用混在同一轮"

**这个问题在 Codex 里不存在对应约束**，因为 Codex 没有结束调用（见第 1、3 节）。相反，Codex 明确支持一轮里出现多个工具调用：`OutputItemResult.tool_future` 是单个可选 future，v0.63.0 的 `try_run_turn` 则用 `FuturesOrdered` 收集多个工具的 future（L2136-L2137），并以 `supports_parallel_tool_calls`（L1989）与 `Feature::ParallelToolCalls`（L1992-L1999）作为并行开关（[codex.rs](https://fastly.jsdelivr.net/gh/openai/codex@rust-v0.63.0/codex-rs/core/src/codex.rs)）。所以准确的表述是：**"结束"在 Codex 语义里就是"这一轮采样没有任何工具调用"，它天然不可能与工具调用共存，因此无需（也确实没有）特殊校验。**

---

## 对 code_agent 的推论

> 以下是研究推论，不是 Codex 的事实。

1. **`finish_task` 不是必需品，而是可选的设计权衡。** Codex 用"没有工具调用 ⇒ 结束"这一隐式信号，代价是：模型如果长文本说完却仍想继续做事、或在不该停的时候停下，循环无法区分。Codex 用另外的机制兜住这个代价——Stop hook（`run_turn_stop_hooks`，`should_block`/`should_stop` 可强制继续或停止）、pending input 续跑、token 超限自动压缩后 `continue`。code_agent 若要引入 `finish_task`，本质是在用显式工具换取"结束意图可被校验/可携带结构化结论"，这与 Codex 路线是两种不同取舍，不冲突。
2. **若引入 `finish_task`，Codex 提示了一个必须处理的问题：成功与中止必须是两个独立维度。** Codex 用 `TurnComplete`（可带 `error`）与 `TurnAborted`（带 `reason`）两个事件区分"说完了"和"被打断"，并额外维护 `ThreadIdleCause::{Completed, Failed, Interrupted}`。code_agent 的 `events.py` 若只设计一个"结束事件"，就会丢掉这个区分；建议至少区分"正常结束/失败结束/被中断结束"。
3. **`finish_task` 与普通工具混在同一轮的限制，Codex 无法提供先例。** 由于 Codex 没有该工具，任何"finish_task 必须是本轮唯一调用"的规则都需要 code_agent 自己定，并自己写校验与测试；不能引用 Codex 作为依据。
4. **协议层"结束"与"模型不再调用工具"是解耦的。** Codex 的做法值得借鉴：判定发生在 agent 循环内部（`needs_follow_up`），而"结束"作为**事件**从统一收尾点广播（`on_task_finished`），任务实现本身不负责发结束事件。这样 CLI、app-server、rollout 记录都从同一处拿到终止信号，不会出现某条路径漏发。code_agent 若把结束事件散落在各执行路径里，容易出现漏发或重发。
5. **`task_complete` 这个名字在 Codex 里是事件而非工具**，如果 code_agent 文档或代码里出现 `task_complete`，要注意别把它当成工具名去接线。

---

## 未能确认的部分

1. **GitHub 直连不可用，但已通过代理读到原文**：`github.com`、`raw.githubusercontent.com`、`api.github.com` 在本机 DNS 均解析到 `127.0.0.1`（被 hosts 屏蔽），`cdn.jsdelivr.net` 同样被拒。实际用了两条通道：(a) jsDelivr 第三方 CDN 镜像 `fastly.jsdelivr.net/gh/openai/codex@<ref>/<path>`；(b) **gh-proxy 代理** `gh-proxy.com/https://raw.githubusercontent.com/...` 与 `gh-proxy.com/https://api.github.com/...`。两通道对 `main` 版 `protocol.rs` 与 `session/turn.rs` 给出的正文与行号**完全一致**，两通道内容一致，但尚不能据此证明官方独立认证。**遗留限制**：jsDelivr 路径未固定 commit，但与 gh-proxy 原文逐字一致；GitHub 代码搜索 API 的结果落在提交 `a9b0815167c27ab13719dd12d13b63e02b70d221`，而我抓取的 raw 内容是 `main` 的当前状态，两者是否同一 commit 未能验证（搜索结果给出的 sha 是索引快照）。
2. **`main` 分支未锁定 commit**：`main` 是移动目标，本文对 `main` 的结论只对应 2026-10-12 抓到的快照（仓库 `pushed_at` 为 2026-10-11T23:45:00Z）。`rust-v0.63.0` 是固定 tag，结论更稳定。
3. **未能获取官方 issue 页面**：搜索结果提示存在 openai/codex issue #13475《Session hangs silently after successful tool_call_output (no final_answer/task_complete)》与 #14691，前者标题与本主题高度相关（工具输出已回传但循环等不到结束信号而静默挂起），但本文使用的两条通道都只代理 raw/API 内容，**未成功读取 issue 正文**，无法确认其复现细节与维护者结论。本文结论完全没有依赖它，仅在此记录线索。
4. **未验证运行时行为**：本文全部结论来自静态源码阅读，**没有**编译或运行 Codex，也没有做实验确认 `needs_follow_up` 在真实会话中的行为。
5. **未找到"最大步数"这一退出条件**：`run_turn` 循环内的 6 个出口（L451/L709/L762/L820/L837 及 L719 的 `return`）没有一个以步数或迭代次数为条件；token 超限走 `continue` + 自动压缩而非退出。因此我只敢说"**在已读到的 `run_turn` 循环内不存在步数上限**"，不能断言 Codex 全局没有步数/预算限制——它可能存在于配置层、`step_context`、`context_window` 或 `input_queue` 等其他模块，本文未追查。
6. **`TurnAbortReason` 的完整取值未逐一枚举**：经 gh-proxy 搜索确认 `enum TurnAbortReason` 的定义仍在 `codex-rs/protocol/src/protocol.rs`（另一处命中是 TUI 的 `chatwidget.rs`，属使用方）。但本文没有完整读取 `main` 版该枚举的定义全文，故 v0.63.0 的三值（`Interrupted`/`Replaced`/`ReviewEnded`）是确定值，`main` 的完整列表（已知至少还有 `BudgetLimited`）标注为不确定。
7. **内置工具名清单不完整**：`spec_plan.rs` 的注册点给的是 **handler 类型名**（如 `ExecCommandHandler`），我没有逐一抓到每个 handler 对应的对外工具字符串名，因此本文只用类型名，未断言诸如 `shell`、`apply_patch` 之类的线上工具名。
8. **未覆盖非 Rust 实现**：仓库内 `codex-cli/`（TypeScript）与 app-server 的客户端侧逻辑未纳入本次核查，"客户端如何消费 `task_complete` 事件"未确认。
9. **`task_complete` 关键词的 18 处命中未逐条阅读**：搜索确认这些命中分布在 `codex-rs/docs/protocol_v1.md`、`tasks/review.rs`、`tui/src/chatwidget*.rs` 及其测试等文件，属事件名与消费方逻辑，**没有任何一处是工具定义**；但我没有逐条打开确认。
