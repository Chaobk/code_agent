# 编程 Agent 的"任务结束信号"业界对照与设计建议

> 复核说明（2026-10-12）：本报告是来源范围不同的案例对照，不是行业占比调查。循环结束、程序退出、产物提交和任务验收成功分别判断；各项目的错误/中断枚举不直接证明能表达任务 blocked。未完整核实的结论以分来源文件限定为准。

研究日期：2026-10-12。
原始调研动机：当时的 code_agent 计划（`PLAN.md` M2a）要新增 `finish_task` 控制调用、移除 M1 的 `END_TASK` 文本标记。在动手前先核实业界主流 Agent 到底怎么做，避免把"我们自己想要的东西"写成"业界标准"。

配套文件（每个文件只记录该来源自身的事实，本文负责横向对照）：

- [completion-signal-codex.md](completion-signal-codex.md) —— OpenAI Codex（Rust 源码，含行号）
- [completion-signal-opencode.md](completion-signal-opencode.md) —— OpenCode（TypeScript 源码）
- [completion-signal-firstparty.md](completion-signal-firstparty.md) —— Pi、Roo Code（官方文档）
- [completion-signal-others.md](completion-signal-others.md) —— Pi 源码补充、Cline 两条线路、Aider、SWE-agent、Claude Code

> **本次调研的一个方法论收获**：`github.com` / `raw.githubusercontent.com` / `api.github.com` 在本机被 hosts 屏蔽到 127.0.0.1，但通过 `https://gh-proxy.com/<原始URL>`、`https://ghproxy.net/<原始URL>`、`https://fastly.jsdelivr.net/gh/<owner>/<repo>@<ref>/<path>` 可以读到**真实源码**，并用代码搜索辅助核查工具名称；未命中只能作为有限的否定证据。本次结论绝大多数是源码级，而非文档转述。

## 一、结论对照表

| 项目 | 结束机制 | 显式完成工具/命令 | 运行/提交状态（不等于验收成功） | 强制独占一轮 |
|---|---|---|---|---|
| **OpenAI Codex** | `needs_follow_up = model_needs_follow_up \|\| has_pending_input`；为假则 `break`（`session/turn.rs` L578 → L661 → L762） | **无**（`finish_task` 全仓库 0 命中） | 区分：两个并列事件 `TurnComplete`（可带 `error`）与 `TurnAborted(reason)` | 不存在该问题（没有完成调用） |
| **OpenCode** | 本轮没有需要本地执行的 tool call（布尔 `needsContinuation`） | **无**（`finish_task`/`task_complete` 0 命中） | `Outcome` 枚举 + 中断原因枚举 `["user","shutdown","superseded","inactivity"]` | 不存在；同轮可并发多工具 |
| **Pi** | 无工具结果且无排队消息 → run 结束；另有工具级 `terminate` 提前结束 | **无**（但有 `terminate` 标志，见下） | **否**；失败是硬退出 `stopReason: "error"/"aborted"` | 否；单条消息可多工具，默认并行 |
| **Cline（VS Code 线路）** | **不是**"无工具即结束"：无工具调用会被判错并注入 `noToolsUsed()` | **有**：`attempt_completion(result, command?)` | 部分；"放弃/阻塞"没有对应工具 | **是，运行时代码强制**（`didAlreadyUseTool`） |
| **Cline（SDK/ClineCore 线路）** | 专门完成工具 | **有**：`submit_and_exit({summary, verified})` | 弱（`verified` 布尔） | 未能确认 |
| **Aider** | 无专门完成工具；编辑/验证后无 reflected_message 时结束当前任务，`/exit`、`/quit` 退出程序 | **无** | 否 | 不适用 |
| **SWE-agent** | 完成命令 `submit` + 环境哨兵 `<<SWE_AGENT_SUBMISSION>>`；`exit` 表示放弃 | **有**：`submit`（无参数） | **是，粒度最细**：`exit_status` 一大族 | 已读解析器每轮一个 action；submit 与其它 shell 操作混用限制未核实 |
| **Claude Code / Agent SDK** | 模型不再发起工具调用 | 无（`EndConversation` 是会话级终止，不是任务级） | 是（`ResultMessage.subtype`） | 否 |

**本次样本的共同模式之一**是"没有需要继续处理的工具调用或输入时结束"；样本不足以推断全行业占比（Codex、OpenCode、Pi、Claude Code），结束语义由**协议事件/字段**承载；显式完成工具出现在**交互形态需要它**的地方——Cline 的 IDE 结对模式（每轮一个工具、完成后要出卡片和反馈）、SWE-agent 的基准评测（需要可机器判定的 `exit_status`）。

## 二、六个关键发现（每条都有对应文件里的链接证据）

### 1. Codex：结束与成功是正交的两个维度

- 判定：模型流式返回的每个 item 过 `handle_output_item_done`，出现工具调用就 `needs_follow_up = true`。
- **退出条件比"没有工具调用"更严**：`let needs_follow_up = model_needs_follow_up || has_pending_input;`（`session/turn.rs` L578）→ L661 `if !needs_follow_up {` → L762 `break;`。即"模型没有工具调用 **且** 没有待处理用户输入"才结束。
- `EventMsg::TurnComplete(TurnCompleteEvent)` 的序列化名是 `"task_complete"`（别名 `"turn_complete"`）——**`task_complete` 是事件名，不是工具名**，这是最容易踩的坑。
- `TurnCompleteEvent.error: Option<ErrorEvent>`：任务返回 `Err` 不让结束信号消失，先发 `Error`，仍然发 `TurnComplete` 并带上错误。
- **反直觉**：`run_turn` 的 6 个循环出口没有一个以步数为条件——所读 run_turn 未见步数退出条件，不能据此排除外围预算或运行限制；token 超限走 `continue` + 自动压缩。

### 2. OpenCode：判据是一个布尔值，上限不用异常

- `needsContinuation: boolean`：`StepRecord` 产出 → `Outcome.Completed` 传递 → `runStep` 消费。
- **步数上限的处理可作为对照方案**：命中上限时不是抛异常，而是把 `toolChoice` 设为 `"none"` 并注入 `MAX_STEPS_PROMPT`，让模型被迫用纯文本收尾；由于 `needsContinuation` 被前置条件 `toolChoice?.type !== "none"` 强制为假，这一轮自然结束。

### 3. Pi：`terminate` 是另一种整批终止设计

- 循环：`while (hasMoreToolCalls || pendingMessages.length > 0)`，其中 `hasMoreToolCalls = !executedToolBatch.terminate`。
- `AgentToolResult.terminate` 的注释写明：*"Early termination only happens when every finalized tool result in the batch sets this to true"* ——**只有整批工具结果都声明终止才提前结束**。
- 边界：一轮可以有多个工具调用，但只有每个已结算结果都带 terminate=true 才提前终止。若只有完成调用置 true、普通探查未置 true，整批不会提前结束；它不是混用完成调用即可结束的现成方案。

### 4. Cline：所研究的 VS Code 线路限制每条消息使用一个工具

- 判定完全相反：**模型不调用工具 ≠ 结束**，会注入 `formatResponse.noToolsUsed()` 并 `consecutiveMistakeCount++`。
- 强制独占：`didAlreadyUseTool` 标志拦截同一消息里的第二个工具，回 `formatResponse.toolAlreadyUsed()`；一旦已有工具结果，之后的文本块也被丢弃。`main` 的措辞是 "Only one tool may be used per message"。
- 代价很直接：一条消息里后面所有内容都被丢掉。**照抄它就等于推翻本项目"单轮可多工具"的前提。**
- 另一坑：Cline 现在有**两条线路**——VS Code 扩展仍是 XML 的 `attempt_completion`，新的 SDK/ClineCore 已换成原生工具 `submit_and_exit({summary, verified})`；官方 legacy 名单故意没列 `attempt_completion`，容易误判为已删除。

### 5. SWE-agent：`exit_status` 区分提交与停止原因

- `submit` 是 `signature: "submit"` + `arguments: []`（无参数），环境侧靠哨兵 `<<SWE_AGENT_SUBMISSION>>` 确认。
- `exit_status` 区分一大族：`submitted` / `submitted (<prev>)`（出错也尽力自动提交）/ `exit_command`（模型放弃）/ `exit_cost` / `exit_command_timeout` / `exit_context` / `exit_format` / `exit_error`。
- 取舍很明确：**除 `exit_command` 外，几乎所有异常都不是"干净失败"，而是"尽力自动提交"**。

### 6. Aider / Claude Code：没有模型侧完成信号也能用

- Aider 无专门的模型侧完成工具；当前任务在编辑及验证后没有待修正的 reflected_message 时结束。`/exit`、`/quit` 是退出应用命令，不能当作任务循环的结束机制。
- Claude Code / Agent SDK 是"模型不再发起工具调用"这一派，收尾状态在 `ResultMessage.subtype` 上。

## 三、对 code_agent 的设计建议（研究推论，不是上述项目的事实）

用户在本轮复核后已确认采用正常 stop 且无工具调用的结束方式，finish_task 留作按需增强。以下为原调研提出的备选权衡；结构化结果、额外总结请求和整批终止不自动成为 M2a 必做项，以最新 PLAN.md 为准。

1. **`PLAN.md`"M2a 改为结构化 finish_task"要改写理由。** 不能表述为业界标准——准确表述是"我们希望结束意图可校验、可携带结构化结论"，这是一个**可选权衡**。可选的反面证据很充分：Codex、OpenCode、Pi、Claude Code 都没有这个工具。
2. **先把两件事拆开，再决定要不要工具**：
   - **循环终止**：可以选择 finish_reason=stop 且无工具调用即正常结束；M1 已提交实现要求 END_TASK 末行，没有标记的纯文本会继续请求。主目录当前未提交实现正在试验移除标记。删掉文本标记即可，**不需要新工具**；
   - **收尾申报**：只有当 `-p`/CLI（以及 M5 的轨迹与恢复）确实需要程序化判定"完成 vs 阻塞"时，才值得引入一个显式申报动作。
3. **若引入 `finish_task`，采用 Codex 的正交设计**：结束 = 一个事件；成功与否 = 事件上可选的 `error`/状态字段。**不要**把 blocked 表达成"没结束"。同时参考 SWE-agent 把状态编成一小族可读值（`completed` / `blocked` / `max_steps` / `interrupted`），而不是一个布尔。
4. **区分两种独占规则。** Cline 的全局每消息一个工具，与本项目原计划仅要求 finish_task 独占一轮不同：后者仍允许普通工具批量调用。Pi 的整批同意也需要每个结果明确终止，不能直接解决 finish_task 与普通调用混用。哪种规则适合本项目须单独决定；不限制单轮数量也不等于并行执行。
5. **`max_steps` 的收尾方式可以单独比较，不自动成为当前阶段前置条件。** 现在是 `raise RuntimeError`，OpenCode 是"最后一轮禁工具 + 注入收尾提示"，Codex 循环里甚至没有步数条件。改成前者能得到可解释的收尾，且是行为级别的可测改进。
6. **事件层（M2b）可借鉴的现成形状**：`ToolResultEvent` 携带 `tool_call_id` + 工具名 + `ToolResult` + `duration_ms`（对齐 Pi 的 `tool_execution_end`）；run 级事件区分**正常结束 / 失败结束 / 被中断**，而不是用一个字符串概括。
7. **最小可行路径（推荐）**：
   - 第一步：删除 `END_TASK`，正常结束 = `finish_reason=stop` 且本轮无工具调用；`run()` 返回最后一条助手消息文本；
   - 第二步：让 `Agent.run()` 返回结构化结果（状态 + summary），CLI 据此定退出码；`max_steps` 改为"禁工具 + 注入收尾提示"；
   - 第三步（可选，需真实失败案例支撑）：若模型频繁"该停不停/不该停却停"，再引入 `finish_task`，并单独定义混用和错误回灌语义；Pi 的整批终止仅作为对照。

## 四、可信度说明

- **源码级证据**：Codex（`gh-proxy` 官方 raw + jsDelivr 镜像，两通道正文与行号一致）、OpenCode（`gh-proxy` + blob API）、Pi（`packages/agent/src/agent-loop.ts` 等）、Cline（tag `v3.17.0` 与 `main`）、SWE-agent（`tools/submit/config.yaml`、`sweagent/agent/agents.py`）、Aider（`aider/coders/base_coder.py`）。
- **文档级证据**：Roo Code（`docs.roocode.cn`）、Claude Code（`code.claude.com`）、pi.dev 文档页。
- **各文件都写了"未能确认"清单**，未凭记忆补全。最要紧的三条：Cline `main` 上 `attempt_completion` 的参数 schema 定义文件未定位；Cline SDK 线路是否有独占强制未确认；SWE-agent 与 Pi 是否有步数上限未确认。
- 本文所有"对 code_agent 的建议"都是推论，不是被引用项目的既有结论。
