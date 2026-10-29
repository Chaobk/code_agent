# 其它编程 Agent 的任务结束信号研究

> 本轮复核限定：第三方代理、镜像及包 CDN 是传输渠道，不能仅凭地址或返回 SHA 宣称内容已获官方认证。原稿的 main 与固定版本结论分别按其范围理解；模型自述 verified、正常运行结束和产物 submitted 都不等于外部任务验收成功。

研究日期：2026-10-12。范围：Pi（earendil-works/pi）、Cline、Aider、SWE-agent，另附 Claude Code / Anthropic Agent SDK 的官方文档说明。Codex 与 OpenCode 由另两条研究线负责，本文不涉及。

来源限制说明：本机 `github.com`、`api.github.com`、`raw.githubusercontent.com` 均被解析到非公开地址，无法直连；实际抓取时改用 `https://gh-proxy.com/` 前缀代理 GitHub 原始文件与 REST API（原稿记录了仓库元数据与 commit SHA，但本轮未独立认证代理返回内容）。官方文档站（pi.dev、docs.cline.bot、code.claude.com、swe-agent.com、aider.chat、docs.roocode.cn、unpkg.com）可直连。每条结论下方标注了它来自**实际抓取的源码**、**实际抓取的官方文档**还是**未能确认**。源码分支与文档会随时间变化，这里记录的是查阅当时的内容；引用 `main` 即为可变快照，引用 tag/版本号的会写明。

## 结论

| 项目 | 结束机制类别 | 显式完成工具/命令名 | 是否区分成功与阻塞 | 是否要求独占一轮 |
| --- | --- | --- | --- | --- |
| **Pi** | (a) 模型不再发起工具调用即结束；另有 (d) 工具 `terminate` 提前终止、`finishTurn` 回调强制结束 | 无 | 否。失败以 `stopReason: "error" / "aborted"` 作为硬退出，不是"完成"的一种 | 否。单条 assistant 消息可含多个工具调用，默认并行执行 |
| **Cline（VS Code 扩展线路）** | (b) 专门完成工具 | `attempt_completion` | 部分。完成用 `attempt_completion`，需要用户输入用 `ask_followup_question`；没有"放弃/阻塞完成"工具 | 是。运行时用 `didAlreadyUseTool` 强制"每条消息只能有一个工具"，源码级实现 |
| **Cline（SDK / ClineCore 线路）** | (b) 专门完成工具 | `submit_and_exit` | 是（弱）。入参含布尔 `verified` 表达"是否已验证" | 未能确认是否有硬性独占约束 |
| **Aider** | (d) 其它：无模型侧完成信号，一轮由"模型停止输出 + aider 解析编辑"界定 | 无。`/exit`、`/quit`、Ctrl-C 都是**用户**侧命令 | 否。只有"格式不合法 → 反射重试"和"用户中断" | 不适用（模型不做工具调用，编辑格式本身就是输出协议） |
| **SWE-agent** | (b) 专门完成命令 `submit`，环境侧以 `<<SWE_AGENT_SUBMISSION>>` 哨兵确认；另有 (d) `exit` 命令表示放弃 | `submit`（无参数） | 是。`exit_status` 区分 `submitted` 与 `exit_command`/`exit_forfeit`/`exit_cost`/`exit_timeout` 等一大族 | 已读 action parser 每轮一个动作；submit 混用限制未核实 |
| **Claude Code / Agent SDK**（对照） | (a) 模型产生"无工具调用"的回复即结束；`ResultMessage.subtype` 报告终止原因 | 无（`EndConversation` 是会话级终止，不是任务完成工具） | 是。`success` / `error_max_turns` / `error_max_budget_usd` / `error_during_execution` | 否。文档明确支持单轮多工具调用（只读并发、写操作串行） |

一句话概括：**Python 侧（Aider）根本没有完成信号；Pi 和 Claude Code 走"没有工具调用就结束"；Cline 和 SWE-agent 是显式完成工具的少数派，而 所检查 Cline VS Code 版本对所有工具实行每消息一个工具的运行时限制；这与仅要求某个完成调用独占不是同一规则**。

## pi

结论（判定对象）：本文的 "pi" 指 **Earendil Inc. 的 Pi**，官网 <https://pi.dev>，npm 包 `@earendil-works/pi-coding-agent`，仓库 `earendil-works/pi`（前身 `badlogic/pi-mono`，作者 Mario Zechner）。判定依据：仓库 README 顶部指向 `https://pi.dev`，包表列出 `@earendil-works/pi-agent-core`、`@earendil-works/pi-coding-agent`，`packages/agent/package.json` 中 `author: "Mario Zechner"`、`repository: git+https://github.com/earendil-works/pi.git`。用户列表中的其它候选（Charm 的 crush 前身、`pi-ai` 独立包等）都不是这个"coding agent CLI"，故排除。

1. **结束机制类别：(a) 模型不再发起工具调用即结束**，另有两条 (d) 补充路径（见第 4 点）。
2. **无显式完成工具。** `packages/coding-agent/src/core/tools/` 目录下只有 `bash.ts`、`edit.ts`、`edit-diff.ts`、`file-mutation-queue.ts`、`find.ts`、`grep.ts`、`ls.ts`、`output-accumulator.ts`、`path-utils.ts`、`powershell.ts`、`read.ts`、`render-utils.ts`、`tool-definition-wrapper.ts`、`truncate.ts`、`write.ts` 与渲染器目录，没有任何 finish/complete/submit 类工具。系统提示默认工具集是 `["read", "bash", "edit", "write"]`。**证据类型：实际抓取源码。**
3. **循环实现**（`packages/agent/src/agent-loop.ts` 的 `runLoop`）：内层 `while (hasMoreToolCalls || pendingMessages.length > 0)`；`hasMoreToolCalls` 由 `hasMoreToolCalls = !executedToolBatch.terminate` 决定；内层退出后检查 follow-up 队列，无 follow-up 且无 `explicitContinuation` 时 `break`，随后 `emit({ type: "agent_end", messages: newMessages })`。关键片段：

   ```ts
   // packages/agent/src/agent-loop.ts
   hasMoreToolCalls = false;
   if (toolCalls.length > 0) {
       const executedToolBatch = ...await executeToolCalls(...);
       toolResults.push(...executedToolBatch.messages);
       hasMoreToolCalls = !executedToolBatch.terminate;
   }
   ...
   // Agent would stop here. Check for follow-up messages.
   const followUpMessages = (await config.getFollowUpMessages?.()) || [];
   if (followUpMessages.length > 0) { ... continue; }
   if (explicitContinuation) { explicitContinuation = false; continue; }
   break;
   ...
   await emit({ type: "agent_end", messages: newMessages });
   ```

   官方文档把这个规则一句话写清：*"Pi records the response, executes each tool call, and records the results. That completes one turn. If tool results or queued messages require another model request, Pi starts another turn. Otherwise, the run ends."*（How Pi Works 页）。**证据类型：实际抓取源码 + 实际抓取官方文档。**
4. **不区分"成功完成"与"阻塞/放弃"。** 失败不是一种完成，而是硬退出：`if (message.stopReason === "error" || message.stopReason === "aborted")` 时直接发 `turn_end` 与 `agent_end` 并 `return`。`Agent.abort()` 触发 `AbortController`，`Agent.handleRunFailure` 造出一条 `stopReason: aborted|error` 的 assistant 消息。**证据类型：实际抓取源码**（`agent-loop.ts`、`packages/agent/src/agent.ts`）。
5. **没有"完成信号必须独占一轮"的约束。** 相反，`AgentToolResult` 与 `afterToolCall` 的 `terminate` 字段注释明确写着 *"Early termination only happens when every finalized tool result in the batch sets this to true"* —— 即一批工具**全部**声明终止时才提前结束，这正说明一条消息里允许多个工具调用。`toolExecution` 默认 `"parallel"`。**证据类型：实际抓取源码**（`packages/agent/src/types.ts`）。
6. **其它终止路径**：
   - `stopReason === "error" | "aborted"` 立即结束（源码）。
   - `config.finishTurn` 返回 `{ action: "end" }` 时，`if (decision?.action === "end")` 直接 `agent_end` 返回；这是扩展可以主动结束 run 的钩子（源码 + `types.ts` 的 `AgentTurnDecision` 类型注释）。
   - `abort()` 用户中断；`Escape` 在交互模式下停止当前任务（文档）。
   - 队列：`steer()`、`followUp()`；follow-up 队列非空时循环继续（源码 + SDK 文档）。
   - 自动重试/恢复/压缩可在 `agent_end` 之后继续，因此 SDK 文档建议宿主用 `agent_settled`（"final and notification-only"）判断"Pi 不会再自动继续"，而 `agent_before_settle` 是最后一个可改写结果的边界（文档）。
   - **步数上限：未能确认存在。** 我在 `packages/agent/src/agent.ts`、`agent-loop.ts`、`types.ts` 中未见到 max-turns/step-limit 一类字段；我没有穷尽整个仓库，故不下"没有"的结论。

**证据（带链接）**

- 仓库 README（实际抓取）：<https://gh-proxy.com/https://raw.githubusercontent.com/earendil-works/pi/main/README.md>
- Agent 循环源码（实际抓取）：<https://gh-proxy.com/https://raw.githubusercontent.com/earendil-works/pi/main/packages/agent/src/agent-loop.ts>
- Agent 封装与失败处理（实际抓取）：<https://gh-proxy.com/https://raw.githubusercontent.com/earendil-works/pi/main/packages/agent/src/agent.ts>
- 类型与 `terminate` 语义（实际抓取）：<https://gh-proxy.com/https://raw.githubusercontent.com/earendil-works/pi/main/packages/agent/src/types.ts>
- 内置工具目录清单（实际抓取 REST API）：<https://gh-proxy.com/https://api.github.com/repos/earendil-works/pi/contents/packages/coding-agent/src/core/tools?ref=main>
- 默认工具集与提示构造（实际抓取）：<https://gh-proxy.com/https://raw.githubusercontent.com/earendil-works/pi/main/packages/coding-agent/src/core/system-prompt.ts>
- `packages/agent/package.json`（实际抓取，确认作者与仓库）：<https://gh-proxy.com/https://raw.githubusercontent.com/earendil-works/pi/main/packages/agent/package.json>
- 官方文档（实际抓取）：[How Pi Works](https://pi.dev/docs/latest/how-pi-works)、[SDK](https://pi.dev/docs/latest/sdk)、[Extensions](https://pi.dev/docs/latest/extensions)、[Use Pi in the terminal](https://pi.dev/docs/latest/usage)、[Message Types](https://pi.dev/docs/latest/message-types)

## Cline

Cline 目前有**两条并行线路**，必须分开说：VS Code 扩展（旧 XML 工具协议，`attempt_completion`）与 SDK / ClineCore（原生工具调用，`submit_and_exit`）。

### 线路一：VS Code 扩展的 `attempt_completion`

1. **类别：(b) 专门的完成工具。**
2. **确切名字与参数 schema**：工具名 `attempt_completion`，两个参数——
   - `result`：**必填**，字符串，最终结果描述；提示词要求"不要以问题或继续对话的邀请结尾"。
   - `command`：**可选**，字符串，一条用于现场演示结果的 CLI 命令；提示词要求"不要用 `echo`、`cat` 这类只打印文本的命令"。
   完整原文（tag `v3.17.0` 的系统提示）：*"Parameters: - result: (required) The result of the task... - command: (optional) A CLI command to execute to show a live demo of the result to the user."*；用法示例 `<attempt_completion><result>...</result><command>...</command></attempt_completion>`。**证据类型：实际抓取源码（tag v3.17.0）。**
   在 `main` 分支上该工具仍然存在：`apps/vscode/src/shared/tools.ts` 的 `ClineDefaultTool` 枚举里是 `ATTEMPT = "attempt_completion"`；`apps/vscode/src/core/prompts/responses.ts` 的 `noToolsUsed()` 里仍写着 *"If you have completed the user's task, use the attempt_completion tool."*，`toolUseInstructionsReminder` 里也以 `<attempt_completion><result>...</result></attempt_completion>` 作示例。**当前 `main` 上该工具的参数定义文件我未能定位（见"未能确认的部分"）。**
3. **是否区分成功与阻塞**：部分区分。完成用 `attempt_completion`；需要用户补充信息用 `ask_followup_question`（`question` 必填、`options` 可选）；Plan 模式用 `plan_mode_respond`（`response` 必填）。**没有**"我放弃了 / 我被阻塞"的完成工具。**证据类型：实际抓取源码（v3.17.0 系统提示）+ 实际抓取官方文档**（Roo Code 分支文档，见下）。
4. **"完成信号必须独占一轮"——提示词层面有要求，运行时层面有强制**：
   - 提示词层面（v3.17.0 `src/core/prompts/system.ts`）：*"You can use one tool per message"*、*"If multiple actions are needed, use one tool at a time per message"*，并且 `attempt_completion` 条目单独写着 *"IMPORTANT NOTE: This tool CANNOT be used until you've confirmed from the user that any previous tool uses were successful."*
   - **运行时强制**（v3.17.0 `src/core/task/index.ts`）：工具执行时用实例标志 `didAlreadyUseTool` 拦截同一消息里的第二个工具，并回给模型 `formatResponse.toolAlreadyUsed(block.name)`；一旦已有工具结果，之后的文本块也被丢弃：

     ```ts
     if (this.didAlreadyUseTool) {
         // ignore any content after a tool has already been used
         this.userMessageContent.push({
             type: "text",
             text: formatResponse.toolAlreadyUsed(block.name),
         });
         break;
     }
     ...
     const pushToolResult = (content: ToolResponse) => {
         ...
         // once a tool result has been collected, ignore all other tool uses since we should only ever present one tool result per message
         this.didAlreadyUseTool = true;
     }
     ```

     `main` 分支的 `responses.ts` 保留了这句话的最终措辞：*"Tool [X] was not executed because a tool has already been used in this message. Only one tool may be used per message."* 所以这是**代码强制**，不只是提示词建议。**证据类型：实际抓取源码。**
5. **其它终止路径**（v3.17.0 `src/core/task/index.ts`）：
   - `initiateTaskLoop()` 是 `while (!this.abort)`，靠 `recursivelyMakeClineRequests` 返回的 `didEndLoop` 跳出；代码注释把规则写得很直白：*"unless there's an attempt_completion call, we keep responding back to him with his tool's responses until he either attempt_completion or does not use anymore tools. If he does not use anymore tools, we ask him to consider if he's completed the task and then call attempt_completion"*。
   - 也就是说**"模型不再调用工具"并不结束任务**：没有工具调用时注入 `formatResponse.noToolsUsed()`（其正文要求模型改用 `attempt_completion` 或 `ask_followup_question`），并 `consecutiveMistakeCount++`。
   - 上限：同一注释提到 *"There is a MAX_REQUESTS_PER_TASK limit to prevent infinite requests"*；我未能定位该常量的定义文件（见"未能确认的部分"）。
   - 用户中断：`abortTask()` 置 `this.abort = true`。
   - 代码注释还提示一个语义细节：*"For now a task never 'completes'. This will only happen if the user hits max requests and denies resetting the count."* —— 即 `attempt_completion` 是把控制权交还用户（用户可以继续反馈），不是进程级终态。
6. **为什么 Cline 需要显式完成工具（本条是推论）**：Cline 的定位是"IDE 里的结对编程"，每次工具调用都要用户批准，人是循环的一部分。此时"模型停止调用工具"是**歧义**的——可能是在等你回答、可能是没词了、也可能真的做完了。一个显式的 `attempt_completion` 把"我交卷了，请你审阅"变成一个可渲染、可反馈的界面事件。Roo Code（Cline 的分支，工具同名）的官方文档对此描述得很具体：结果以特殊的 `completion_result` UI 格式展示、随后等待用户反馈、*"这会创建一个迭代改进周期，而不是结束对话"*。注意：Roo Code 是分支，不能完全等同于 Cline，但工具名与参数一致，可作为交互语义的旁证。**证据类型：实际抓取官方文档（Roo Code）。**

### 线路二：SDK / ClineCore 的 `submit_and_exit`

1. **类别：(b) 专门的完成工具。**
2. **确切名字与 schema**：工具名 `submit_and_exit`（官方工具表描述为 "Submit a final answer and stop"，分类为 `Completion`）。入参 schema 来自官方仓库 `sdk/packages/core/src/extensions/tools/schemas.ts`（我实际读到的是其已发布产物 `@cline/core` 的 `dist/extensions/tools/schemas.d.ts`）：

   ```ts
   export declare const SubmitInputSchema: z.ZodObject<{
       summary: z.ZodString;
       verified: z.ZodBoolean;
   }, ...>;
   ```

   即 **`summary: string`（必填）+ `verified: boolean`（必填）**，与旧 XML 工具的 `result`/`command` 完全不同。工厂函数为 `createSubmitAndExitTool(executor: VerifySubmitExecutor, config?: Pick<DefaultToolsConfig, "submitTimeoutMs">)`。**证据类型：实际抓取源码（`main` 的 `definitions.ts` 路径由 GitHub code search 命中；schema 定义读自 npm 已发布产物的 `.d.ts`）。**
3. **是否区分成功与阻塞**：`verified` 布尔表达"该结果是否已被验证"，这是 Cline 把"自证"显式编码进完成信号的尝试（仓库中存在提交 `fix(prompt): require verification before completion`，但我只看到搜索摘要，未读到 diff）。没有独立的"放弃"工具。**证据类型：schema 为实际抓取源码；PR 仅为搜索结果摘要。**
4. **独占约束：未能确认。** 旧线路的 `didAlreadyUseTool` 是 VS Code 扩展自身的实现；SDK 线路的工具是普通 `AgentTool`，我在 `@cline/core` 的 `.d.ts` 中没有找到"完成工具必须独占"的声明。

**证据（带链接）**

- Cline v3.17.0 系统提示（实际抓取源码，含 `attempt_completion` 完整参数说明）：<https://gh-proxy.com/https://raw.githubusercontent.com/cline/cline/v3.17.0/src/core/prompts/system.ts>
- Cline v3.17.0 任务主循环与 `didAlreadyUseTool`（实际抓取源码）：<https://gh-proxy.com/https://raw.githubusercontent.com/cline/cline/v3.17.0/src/core/task/index.ts>
- Cline v3.17.0 消息解析（实际抓取源码，展示 XML 工具块的解析方式与 `partial` 语义）：<https://gh-proxy.com/https://raw.githubusercontent.com/cline/cline/v3.17.0/src/core/assistant-message/parse-assistant-message.ts>
- Cline `main` 工具枚举（实际抓取源码，`ATTEMPT = "attempt_completion"`）：<https://gh-proxy.com/https://raw.githubusercontent.com/cline/cline/main/apps/vscode/src/shared/tools.ts>
- Cline `main` 的 `noToolsUsed` / `toolAlreadyUsed`（实际抓取源码，独占一轮的最终措辞）：<https://gh-proxy.com/https://raw.githubusercontent.com/cline/cline/main/apps/vscode/src/core/prompts/responses.ts>
- GitHub code search 命中 `submit_and_exit` 定义位置（实际抓取 REST API）：<https://gh-proxy.com/https://api.github.com/search/code?q=repo%3Acline%2Fcline+%22submit_and_exit%22>
- `@cline/core` 已发布类型（实际抓取 npm 产物）：<https://unpkg.com/@cline/core@0.0.92/dist/extensions/tools/schemas.d.ts>、<https://unpkg.com/@cline/core@0.0.92/dist/extensions/tools/definitions.d.ts>
- 官方文档（实际抓取）：[Tools（内置工具表与"Completion"分类）](https://docs.cline.bot/tools-reference/all-cline-tools.md)、[Overview](https://docs.cline.bot/cline-overview)、[Deprecations](https://docs.cline.bot/resources/deprecations.md)、[llms.txt 索引](https://docs.cline.bot/llms.txt)
- 分支项目文档（实际抓取，仅作交互语义旁证）：[Roo Code attempt_completion](https://docs.roocode.cn/advanced-usage/available-tools/attempt-completion)

## Aider

1. **类别：(d) 其它——Aider 没有模型侧的结构化完成信号。** 一轮的边界是"Aider 把模型输出收完并据此应用编辑"，不是模型调用某个工具。
2. **无显式完成工具/命令（模型可见）。** `aider/coders/` 目录清单里全部是编辑格式实现（`editblock_coder.py`、`udiff_coder.py`、`wholefile_coder.py`、`patch_coder.py`、`architect_coder.py`、`search_replace.py` 等）与对应 `*_prompts.py`，没有任何 finish/summary/done 工具。模型通过输出 SEARCH/REPLACE、udiff、whole-file 等**文本块**来表达"我改了文件"，Aider 再去解析。**证据类型：实际抓取源码（目录清单 + `base_coder.py`）。**
3. **循环实现**（`aider/coders/base_coder.py`，`main` 分支）：外层是人机 REPL，内层的 reflection 循环决定"这一轮是否重来"：

   ```python
   class Coder:
       num_reflections = 0
       max_reflections = 3

       def run(self, with_message=None, preproc=True):
           ...
           while True:
               try:
                   user_message = self.get_input()
                   self.run_one(user_message, preproc)
                   self.show_undo_hint()
               except KeyboardInterrupt:
                   self.keyboard_interrupt()
           ...

       def run_one(self, user_message, preproc):
           self.init_before_message()
           ...
           while message:
               self.reflected_message = None
               list(self.send_message(message))
               if not self.reflected_message:
                   break
               if self.num_reflections >= self.max_reflections:
                   self.io.tool_warning(f"Only {self.max_reflections} reflections allowed, stopping.")
                   return
               self.num_reflections += 1
               message = self.reflected_message
   ```

   关键点：`if not self.reflected_message: break` —— **"这一轮结束了"等于"Aider 没有产生需要模型纠正的反射消息"**，与模型是否声明完成无关。**证据类型：实际抓取源码。**
4. **失败如何表达**：`apply_updates()` 捕获 `ValueError` 时 `num_malformed_responses += 1`、打印 `"The LLM did not conform to the edit format."`、并 `self.reflected_message = str(err)`，于是下一轮把这段话当新输入喂回去，最多 `max_reflections = 3` 次。lint/test 失败也走同一条 `reflected_message` 通道。**证据类型：实际抓取源码。**
5. **是否区分成功与阻塞**：否。没有"完成/放弃"的状态字段；只有"格式合法并应用了编辑"与"格式不合法，重试到超限后放弃"。用户可以用 Ctrl-C 随时中断且部分回复保留在会话里。**证据类型：实际抓取源码 + 官方文档。**
6. **用户侧退出**：官方命令表列出 `/exit` 和 `/quit`（描述均为 "Exit the application"），以及 `/clear`、`/reset`、`/undo` 等会话管理命令。这些是**人在聊天框里打的斜杠命令**，不是给模型的结构化信号——`/exit` 只是让 Aider 退出程序，模型看不到也不需要发它。`Ctrl-C` 中断的安全语义也在官方文档里写明了。**证据类型：实际抓取官方文档。**
7. **其它终止路径**：`FinishReasonLength`（`finish_reason == "length"` 时抛出，流式与非流式两处）、`KeyboardInterrupt`、context window 超限的 LiteLLM 异常。**证据类型：实际抓取源码。**

**证据（带链接）**

- `aider/coders/base_coder.py`（实际抓取源码）：<https://gh-proxy.com/https://raw.githubusercontent.com/Aider-AI/aider/main/aider/coders/base_coder.py>
- `aider/coders/` 目录清单（实际抓取 REST API，用于证明没有完成类 coder）：<https://gh-proxy.com/https://api.github.com/repos/Aider-AI/aider/contents/aider/coders?ref=main>
- GitHub code search 命中 `def cmd_exit` 位于 `aider/commands.py`（实际抓取 REST API）：<https://gh-proxy.com/https://api.github.com/search/code?q=repo%3AAider-AI%2Faider+%22def+cmd_exit%22>
- 官方文档（实际抓取）：[In-chat commands（`/exit`、`/quit`、Ctrl-C 中断）](https://aider.chat/docs/usage/commands.html)

## SWE-agent

1. **类别：(b) 专门的完成命令 `submit`**，配合 (d) 环境侧哨兵与 `exit` 命令。
2. **确切名字与参数 schema**：命令名 `submit`，**没有参数**。`tools/submit/config.yaml` 全文只有 4 行有效内容：

   ```yaml
   tools:
     submit:
       signature: "submit"
       docstring: "submits the current file"
       arguments: []
   ```

   `ToolConfig` 中与之相关的字段（官方 API 文档）：`submit_command: str = 'submit'`（"The command/tool to use to submit the solution."）、`submit_command_end_name: str | None = None`。**证据类型：实际抓取源码 + 实际抓取官方文档。**
3. **实现与判定**：`tools/submit/bin/submit` 是一个 shell 可执行文件（不是 Python 类），做三件事并打印哨兵：

   ```sh
   main() {
       cd $ROOT
       if [ -s "/root/test.patch" ]; then git apply -R < "/root/test.patch"; fi
       git add -A
       git diff --cached > /root/model.patch
       echo "<<SWE_AGENT_SUBMISSION>>"
   }
   ```

   即：回滚测试补丁 → 把所有改动写进 `/root/model.patch` → 打印 `<<SWE_AGENT_SUBMISSION>>`。主循环侧靠 `handle_submission()` 识别这个哨兵：

   ```python
   def handle_submission(self, step: StepOutput, *, observation="", force_submission: bool = False) -> StepOutput:
       step = step.model_copy(deep=True)
       is_submission = self.tools.check_for_submission_cmd(observation or step.observation)
       if is_submission or force_submission:
           try:
               submission = self._env.read_file("/root/model.patch", ...)
           ...
           if submission.strip() != "": step.submission = submission
           else: step.submission = None
           step.observation = submission
           if not step.exit_status: step.exit_status = "submitted"
           elif step.submission: step.exit_status = f"submitted ({step.exit_status})"
           step.done = True
       return step
   ```

   主循环是 `while not step_output.done: step_output = self.step()`。**证据类型：实际抓取源码。**
4. **是否区分成功与阻塞：是，而且粒度很细。** 两个正面路径、一大族失败路径，全部记录在 `StepOutput.exit_status` / `info["exit_status"]`：
   - `submitted`：正常提交（`handle_submission`）。
   - `submitted (<prev>)`：出错后自动提交（`attempt_autosubmission_after_error`，它会先尝试自己 `git add -A && git diff --cached > /root/model.patch` 再调 `handle_submission(..., force_submission=True)`）。
   - `exit_command`：模型直接输出 `exit` 命令表示放弃——`handle_action()` 中 `if step.action.strip() == "exit": step.done = True; step.exit_status = "exit_command"`。
   - `exit_forfeit`（环境里出现 `###SWE-AGENT-EXIT-FORFEIT###` 令牌）、`exit_total_execution_time`、`exit_command_timeout`、`exit_context`、`exit_cost`、`exit_api`、`exit_environment_error`、`exit_error`、`exit_format`。
   注意一个设计取舍：**除了 `exit_command` 之外，几乎所有异常都不是"干净失败"，而是"尽力自动提交"**——`attempt_autosubmission_after_error` 会在环境还活着时补一个 patch 出来，再把 `exit_status` 写成 `submitted (...)`。**证据类型：实际抓取源码。**
5. **混用限制未核实**：单个 action 字符串可能包含复合 shell 命令，不能由此证明 submit 必须独占。所读取的解析路径为：模型输出经 `parse_function`（`ThoughtActionParser` / `ActionOnlyParser` / `type: function_calling`）解析成 `step.thought` 与 `step.action` **单个** action，然后 `handle_action` 执行这一个动作。这与 Cline 的"多工具块但要忽略后面那些"是两种完全不同的解法。**证据类型：实际抓取源码 + 实际抓取官方文档。**
6. **其它终止路径**（均有源码或官方 API 文档支撑）：
   - 格式错误 / 动作被 blocklist 拦截 / bash 语法错误 → 重新询问模型，`max_requeries: int = 3`（`DefaultAgentConfig` / `ShellAgentConfig` 文档默认值 3；源码 `while n_format_fails < self.max_requeries`），超出后 `exit_format`。
   - 连续执行超时：`self._n_consecutive_timeouts >= self.tools.config.max_consecutive_execution_timeouts`（文档默认 3）后抛错 → `exit_command_timeout`。
   - 总执行时间：`forward()` 里 `if self._total_execution_time > self.tools.config.total_execution_timeout: raise _TotalExecutionTimeExceeded()`（文档默认 `total_execution_timeout = 1800` 秒，`execution_timeout = 30` 秒）。
   - 成本：`CostLimitExceededError` → `exit_cost`；`TotalCostLimitExceededError` 直接向上抛（`RetryAgent` 注释强调必须让它冒泡以真正停止）。
   - 用户中断：`KeyboardInterrupt`、`EOFError` 直接 `raise`。
   - **步数上限：未能确认。** 我在官方 API 文档的 `DefaultAgentConfig`、`ToolConfig`、`RunSingleConfig` 字段列表里没有看到 `step_limit` 一类字段，也没有在 `agents.py` 里看到步数检查；但我没有穷尽 1.0 全部配置文件，故不下"不存在"的结论。

**证据（带链接）**

- `submit` 工具定义（实际抓取源码）：<https://gh-proxy.com/https://raw.githubusercontent.com/SWE-agent/SWE-agent/main/tools/submit/config.yaml>
- `submit` 可执行文件（实际抓取源码）：<https://gh-proxy.com/https://raw.githubusercontent.com/SWE-agent/SWE-agent/main/tools/submit/bin/submit>
- `tools/submit/` 目录清单（实际抓取 REST API）：<https://gh-proxy.com/https://api.github.com/repos/SWE-agent/SWE-agent/contents/tools/submit?ref=main>
- `sweagent/agent/agents.py`（实际抓取源码，`handle_submission` / `handle_action` / `run` / `attempt_autosubmission_after_error` / `forward_with_handling` 全在此）：<https://gh-proxy.com/https://raw.githubusercontent.com/SWE-agent/SWE-agent/main/sweagent/agent/agents.py>
- 官方文档（实际抓取）：[Tool configuration（`submit_command`、超时字段）](https://swe-agent.com/latest/reference/tools_config/)、[Agent configuration（`max_requeries`、示例 default.yaml）](https://swe-agent.com/latest/reference/agent_config/)、[Agent class（`handle_submission` 等）](https://swe-agent.com/latest/reference/agent/)、[Configuring tools（tool bundle 结构与 `state` 命令）](https://swe-agent.com/latest/config/tools/)、[Agent tools / ACI](https://swe-agent.com/latest/background/aci/)、[Run single](https://swe-agent.com/latest/reference/run_single_config/)、[文档首页](https://swe-agent.com/latest/)

## Claude Code / Anthropic Agent SDK（仅官方文档，对照用）

本节只引用官方文档，不推测闭源实现。

1. **类别：(a) 模型不再发起工具调用即结束。** 官方"Agent loop"页原文：*"Claude continues calling tools and processing results until it produces a response with no tool calls."*，随后 *"The SDK yields a final AssistantMessage with the text response (no tool calls), followed by a ResultMessage..."*。**证据类型：实际抓取官方文档。**
2. **没有任务完成工具。** 内置工具表（`Agent`、`Bash`、`Read`、`Edit`、`Write`、`Glob`、`Grep`、`WebFetch`、`WebSearch`、`Task*`、`Skill` 等）里没有 finish/complete 类工具。`EndConversation` 是**会话级终止**（"Ends the session, in rare cases of sustained abusive input or when you ask Claude to demonstrate the tool"），不是任务完成信号；官方还特别说明它不触发 `PreToolUse`、也不能被 deny 规则或 `--disallowedTools` 移除。**证据类型：实际抓取官方文档。**
3. **区分成功与各类非正常终止**：`ResultMessage.subtype` 取 `success` / `error_max_turns` / `error_max_budget_usd` / `error_during_execution` / `error_max_structured_output_retries`；只有 `success` 才带 `result` 文本。另有 `stop_reason`（`end_turn` / `max_tokens` / `refusal` 等）表示模型最后一轮的停止原因。**证据类型：实际抓取官方文档。**
4. **无独占一轮约束**：文档明确 One turn 可以请求多个 tool call，且"read-only tools can run concurrently"、"Tools that modify state run sequentially"。**证据类型：实际抓取官方文档。**
5. **其它终止路径**：`max_turns`（只计 tool-use 轮次）、`max_budget_usd`；`Stop` hook（"When Claude finishes responding"）、`StopFailure`（"When the turn ends due to an API error"）。`Stop` hook 可返回 `decision: "block"` + `reason` 阻止收尾，或返回 `additionalContext` 让对话继续——**这是"用钩子延长而不是用工具完成"的另一种思路**。可处理的执行错误可通过结果 subtype 表达；进程崩溃可能没有机会生成或交付 ResultMessage，不能把两者合并成相同保证。

**证据（带链接）**

- [How the agent loop works](https://code.claude.com/docs/en/agent-sdk/agent-loop.md)（实际抓取）
- [Tools reference](https://code.claude.com/docs/en/tools-reference.md)（实际抓取，含 `EndConversation` 行为与内置工具表）
- [Hooks reference](https://code.claude.com/docs/en/hooks.md)（实际抓取，含 `Stop` / `StopFailure` 与决策控制表）
- [Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview)（实际抓取）

## 对 code_agent 的推论与采用情况

本项目已完成流式闭环，采用正常 stop 且无工具调用时结束，RunResult 表达 completed、failed 或 max_steps；取消与内部故障向外传播。当前默认 Bash、顺序执行，不限制单轮调用数量。原稿把项目写成仍在 M2a 的说明已过时。[现行计划](../../PLAN.md)

这些样本展示了隐式循环终止、显式提交命令和 UI 完成反馈等不同设计，无法据此统计行业“多数派”。任务恢复、轨迹或决策模式也不必依赖 finish_task；本项目继续将它保留为可选增强。

若未来需要结构化申报，应分别设计申报字段、实际结束判定与外部成功验证。模型填写 verified 或 submitted 只是一条声明，不能代替测试。SWE-agent 的 submitted 状态也只说明提交路径，不保证产物正确。

每消息单工具、工具执行并发和批次终止是独立取舍。不能把 Cline 的限制或 Pi 的整批 terminate 原样当成本项目要求。事件字段应按实际消费者设计；当前未增加 RunFinished 或“阻塞”完成工具。

Aider 的编辑协议与有限纠错展示了原生工具调用之外的一条路径，可用于理解协议边界。本项目无需改换协议或新增重试机制来对齐它。工具结果继续使用 output/status/truncated，原稿“统一 metadata”并非已采纳契约。

## 未能确认的部分

以下结论我**没有**拿到一手证据，文档里对应位置已标注，不要当作事实使用：

1. **Cline `main` 分支上 `attempt_completion` 的参数 schema 定义文件。** 我确认了它在 `main` 上仍存在（`apps/vscode/src/shared/tools.ts` 的枚举、`responses.ts` 的提示文本），但没找到当前生成 `result` / `command` 两个参数描述的源文件；因此 `result` 必填、`command` 可选这条**只有 tag `v3.17.0` 的系统提示作为源码证据**。尝试过并失败的 URL：`.../main/apps/vscode/src/core/prompts/system.ts`（404）、`.../main/src/core/prompts/system.ts`（404）、`.../main/src/core/prompts/system-prompt/index.ts`（404）、`.../main/apps/vscode/src/core/prompts/system-prompt/index.ts`（404）；`apps/vscode/src/core/prompts` 目录清单（REST API，200）只含 `__tests__/` 与 `responses.ts`，说明提示正文已移出该目录（可能在 `sdk/packages/shared/src/prompt/` 下，我只在 code search 结果里看到该路径出现过，未读取）。
2. **Cline 的 `MAX_REQUESTS_PER_TASK` 常量定义位置与取值。** 只在 v3.17.0 `src/core/task/index.ts` 的注释里被提到。
3. **Cline SDK 线路 `submit_and_exit` 是否有"必须独占一轮"的运行时强制。** 未找到相关判定代码。
4. **Cline PR #9154 `fix(prompt): require verification before completion` 的具体改动。** 只有搜索结果标题，`patch-diff.githubusercontent.com` 经代理返回 403，未读到 diff。
5. **SWE-agent 是否存在步数上限（`step_limit` 一类）。** 官方 API 文档的 `DefaultAgentConfig`、`ToolConfig`、`RunSingleConfig` 字段列表里都没有，但我没有穷尽全部配置文件。
6. **Pi 是否存在全局步数/轮次上限。** 在 `packages/agent/src/` 的三个核心文件里没有见到，未穷尽仓库。
7. **Pi 是否内置任何以"完成任务"为语义的工具。** 依据是 `packages/coding-agent/src/core/tools/` 的目录清单与默认工具集 `["read","bash","edit","write"]`；扩展可以注册任意工具，所以这只说明"内置工具集里没有"。
8. **Roo Code 文档能否代表 Cline。** Roo Code 是 Cline 的分支，工具同名同参数，但它是独立项目；本文只把它当作 `attempt_completion` 交互语义（特殊 UI、等待用户反馈、迭代而非终止）的旁证，并已注明。
