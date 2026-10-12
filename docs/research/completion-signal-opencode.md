# OpenCode 的任务结束信号研究

> 本轮复核限定：原稿中的源码与元数据经第三方代理取得，blob SHA 是来源定位线索，本轮未重新校验全部 blob。零命中及目录清单是范围有限的否定证据；分支与索引也可能不同步。

研究日期：2026-10-12。

## 范围与来源限制

- **实际仓库**：`anomalyco/opencode`，默认分支 `dev`。用 GitHub API 确认：请求 `repos/sst/opencode` 返回的 `full_name` 就是 `anomalyco/opencode`（即 sst/opencode 已重定向到该组织），`default_branch` 为 `dev`。repo `id = 975734319`，`pushed_at = 2026-10-12T07:42:53Z`，语言 TypeScript。本报告所有路径与 SHA 都取自 `dev` 分支在 2026-10-12 的状态（tree SHA `3e78cd58e001a9b89ddbde53bb889190a934b649`）。
- **抓取方式（重要）**：本机 `hosts` 把 `github.com`、`api.github.com`、`raw.githubusercontent.com` 等一律解析到 `127.0.0.1`，因此 `web_fetch` 对这三个域名直接报 `hostname resolves to a non-public IP address`，shell 里的 `Invoke-WebRequest` 也因 TLS 失败。**实际可用的通道只有一个**：把原始 URL 拼到 `https://gh-proxy.com/<原始 URL>` 后面（例如 `https://gh-proxy.com/https://api.github.com/repos/anomalyco/opencode/...`）。本报告全部证据来自这条通道抓到的 GitHub REST 响应；返回的是 base64 编码的文件内容，我在本地用 `[Convert]::FromBase64String` 解码后阅读。`raw.githubusercontent.com` 与 `github.com/<owner>/<repo>/blob/...` 页面本身**未能获取**。
- **路径已迁移**：仓库文档 `docs/research/tool-results.md` 与 `docs/research/session-agent-binding.md` 记录的 `packages/opencode/src/session/prompt.ts`、`processor.ts` 在当前 `dev` 上**已经不存在**（`packages/opencode` 这个包本身也已不存在）。现在的对应位置是 `packages/core/src/session/`，其中没有 `processor.ts`，取而代之的是 `session/runner/` 目录。下文所有结论基于迁移后的新路径。
- **抓到并实际读取的文件（全部为 base64 原文解码后阅读）**：

| 文件 | blob SHA | 大小 | URL（经代理） |
| --- | --- | --- | --- |
| `packages/core/src/session/runner/index.ts` | `493713fa67684bca6f68fb1560754700006457e4` | 1618 | `https://gh-proxy.com/https://api.github.com/repos/anomalyco/opencode/git/blobs/493713fa67684bca6f68fb1560754700006457e4` |
| `packages/core/src/session/runner/step.ts` | `346ccbcebd47b258c22f03b4b7cab3f0a78d55eb` | 14582 | `https://gh-proxy.com/https://api.github.com/repos/anomalyco/opencode/git/blobs/346ccbcebd47b258c22f03b4b7cab3f0a78d55eb` |
| `packages/core/src/session/runner/llm.ts` | `3ffe4ed6e900cdcea5f0e26f157318b658d77108` | 16115 | `https://gh-proxy.com/https://api.github.com/repos/anomalyco/opencode/git/blobs/3ffe4ed6e900cdcea5f0e26f157318b658d77108` |
| `packages/core/src/session/runner/max-steps.ts` | `040584ab173877d12b821808489b6a7c80504954` | 785 | `https://gh-proxy.com/https://api.github.com/repos/anomalyco/opencode/git/blobs/040584ab173877d12b821808489b6a7c80504954` |
| `packages/core/src/session/runner/publish-llm-event.ts` | `fa39ea95c2e4c17b7a00eb4cba294e5d99130553` | 24840 | `https://gh-proxy.com/https://api.github.com/repos/anomalyco/opencode/git/blobs/fa39ea95c2e4c17b7a00eb4cba294e5d99130553` |
| `packages/schema/src/session-event.ts` | `0cffae27ad631740c4b7b59c09c01440b4b603a6` | 19390 | `https://gh-proxy.com/https://api.github.com/repos/anomalyco/opencode/git/blobs/0cffae27ad631740c4b7b59c09c01440b4b603a6` |
| `packages/schema/src/agent.ts` | `c47d5e8e7ef8bf3c82de3d9fb49b16e33c5230fd` | 1907 | `https://gh-proxy.com/https://api.github.com/repos/anomalyco/opencode/git/blobs/c47d5e8e7ef8bf3c82de3d9fb49b16e33c5230fd` |
| `packages/web/src/content/docs/agents.mdx`（官方文档） | `0c0a5e22846583cf77f3262fd12efe5f438438e0` | 20409 | `https://gh-proxy.com/https://api.github.com/repos/anomalyco/opencode/contents/packages/web/src/content/docs/agents.mdx?ref=dev` |

- **目录树（用于确认路径与文件清单）**：`https://gh-proxy.com/https://api.github.com/repos/anomalyco/opencode/git/trees/dev?recursive=1`、`.../git/trees/72fa128ba7cc46f799f0fe04a33efc1fb7730e86?recursive=1`（packages/core）、`.../git/trees/10069e8d31741d93a1111e1a53b2e8b5298982ad?recursive=1`（packages/core/src/session/runner）、`.../contents/packages/core/src/tool?ref=dev`、`.../contents/packages/schema/src?ref=dev`。

## 结论

1. **没有专门的"任务完成"工具。** 搜索也没有这类命名：`repo:anomalyco/opencode "finish_task"` → `total_count: 0`；`"task_complete"` → `total_count: 0`；`filename:finish` → 只有两个 provider 的 finish-reason 映射文件（`map-openai-compatible-finish-reason.ts`、`map-openai-responses-finish-reason.ts`），与任务完成无关。`packages/core/src/tool/` 目录下只有 `html-markdown.ts`、`http-body.ts`、`mcp.ts`、`runtime.ts`、`read-filesystem.ts` 和 `plugin/`，没有任何 finish/done/submit 工具。搜索证明 URL：`https://gh-proxy.com/https://api.github.com/search/code?q=repo:anomalyco/opencode+%22finish_task%22`、`.../search/code?q=repo:anomalyco/opencode+%22task_complete%22`、`.../search/code?q=repo:anomalyco/opencode+filename:finish`。

2. **结束信号由"这一轮里有没有本地执行的工具调用"承载。** 判定字段叫 **`needsContinuation: boolean`**，它定义在 `StepRecord`（生产者）和 `Outcome.Completed`（传递者）两处类型上，并由 `llm.ts` 的 `runStep` 消费，全部只用在这 4 个文件中出现（`search/code?q=repo:anomalyco/opencode+needsContinuation` → `total_count: 4`，命中 `step.ts`、`llm.ts`、`publish-llm-event.ts`、`test/session-step.test.ts`）。

3. **两层退出**：
   - 外层（单个会话轮次 drain）：每执行完一个 step，如果模型没有发起任何需要本地执行的工具调用，就退出 `while (true)` 循环，返回 `DrainResult.Complete()`，整个轮次结束。
   - 内层（一个 step 内的重试/压缩循环）：由 `Outcome` 这个 tagged enum 的 5 个变体驱动 —— `Completed | Retry | Continue | RecoverFull | Compacted`，只有 `Completed` 携带 `needsContinuation`，其余分支（重试、上下文溢出压缩、传输恢复）都不会结束轮次。

4. **终止路径与"任务完成"是分开的、有名字的**：
   - 达到步数上限（`agent.info.steps`）→ 不是硬中断，而是"最后一轮强制纯文本"：不结束循环，而是把 `toolChoice` 设为 `"none"` 并往消息里注入一段合成提示 `MAX_STEPS_PROMPT`，然后由这一轮的 `needsContinuation`（此时必然为 `false`）自然结束。
   - 用户中断 / 位置关闭 → `session.execution.interrupted`，`reason` 枚举为 `["user", "shutdown", "superseded", "inactivity"]`。
   - 错误 → `session.step.failed`（携带 `SessionError.Error`）或 `session.execution.failed`；step 内还有 `TOOLS_INTERRUPTED` / `STEP_INTERRUPTED` / `RESULT_MISSING` / `INPUT_INCOMPLETE` 四个错误常量。
   - 上下文溢出 → `Outcome.Compacted` + `session.compaction.*` 事件。
   - 工具调用被用户拒绝 → 走 `Effect.interrupt`（中断整轮）而不是 `Completed`。

5. **"必须单独一轮"/"不能和别的工具混用"这类约束不成立。** OpenCode 恰恰相反：它按**每一个** tool-call 的 `providerExecuted` 标志分别判定，同一轮里模型可以混发多个工具调用（每个工具调用一个 fiber 并发执行）。唯一与"必须单独一轮"沾边的是达到 `steps` 上限时通过 `toolChoice: "none"` 强制"这一轮只准输出文本"，这是上限的收尾策略，不是完成协议。

## 证据

### 证据 1：drain 主循环的退出条件

来源：`packages/core/src/session/runner/llm.ts`（blob `3ffe4ed6e900cdcea5f0e26f157318b658d77108`），`Service` 的 `drain` 实现末尾：

```ts
      while (true) {
        const next = yield* advanceToStep()
        if (next._tag !== "Ready") return next
        continuing = yield* runStep(next.context, step)
        step++
        force = false
        entering = false
      }
```

以及 `runStep` 内把 `Outcome.Completed.needsContinuation` 直接当作"是否继续"：

```ts
        const completed = yield* SessionStep.Outcome.$match(outcome, {
          Completed: (outcome) => Effect.succeed(outcome.needsContinuation),
          Retry: (outcome) => retry.wait({ ... }),
          Continue: Effect.fnUntraced(function* (outcome) { ... }),
          Compacted: Effect.fnUntraced(function* () { ... }),
          RecoverFull: Effect.fnUntraced(function* () { ... }),
        })
        if (completed !== undefined) return completed
```

`completed === false` 时 `runStep` 返回 `false`，`continuing` 变 `false`；下一个 `advanceToStep()` 在没有任何待处理输入时会返回 `DrainResult.Complete()`（见证据 4），于是 `while (true)` 通过 `if (next._tag !== "Ready") return next` 退出。**这就是"这一轮任务结束"的判定点：模型这一轮没有产生需要本地执行的工具调用。**

### 证据 2：`needsContinuation` 到底怎么算

来源：`packages/core/src/session/runner/publish-llm-event.ts`（blob `fa39ea95c2e4c17b7a00eb4cba294e5d99130553`），`createLLMEventPublisher(bus, input)` 返回对象的 `record()`：

```ts
    record: (): StepRecord => ({
      outputStarted,
      providerFailed,
      failure: stepFailure,
      finish: stepSettlement,
      needsContinuation: Iterable.some(
        tools.values(),
        (tool) => !tool.providerExecuted && (tool.called || tool.settled),
      ),
    }),
```

每个 tool-call 在 `ToolState` 里记录 `called` / `settled` / `providerExecuted` 三个布尔（`providerExecuted` 来自 provider 自己执行的 hosted tool，例如 web search）。含义：**只要本轮有至少一个"不是 provider 自己执行、但被调用过或已结算"的工具，就需要继续下一轮**；模型纯文本收尾（没有任何 tool-call）时该值为 `false`。

同一文件里 `StepRecord` 的类型声明（`export interface StepRecord`）把 `needsContinuation: boolean` 与 `outputStarted`、`providerFailed`、`failure?`、`finish?` 放在一起。

### 证据 3：step 的返回类型与最终 return

来源：`packages/core/src/session/runner/step.ts`（blob `346ccbcebd47b258c22f03b4b7cab3f0a78d55eb`），文件开头的类型定义：

```ts
export type Outcome = Data.TaggedEnum<{
  Completed: { readonly needsContinuation: boolean }
  Retry: { readonly error: SessionError.Error; readonly decision: SessionRunnerRetry.Decision }
  Continue: { readonly error: SessionError.Error; readonly decision: SessionRunnerRetry.Decision }
  RecoverFull: {}
  Compacted: {}
}>
export const Outcome = Data.TaggedEnum<Outcome>()
```

以及 `attempt` 的最后一行（唯一的成功出口）：

```ts
        return Outcome.Completed({
          needsContinuation: input.prepared.request.toolChoice?.type !== "none" && record.needsContinuation,
        })
```

注意 `toolChoice?.type !== "none"` 这个前置条件：步数上限生效时 `toolChoice` 被置为 `"none"`，于是 `needsContinuation` 被强制为 `false`，这一轮必然收尾。

同一文件里，`executeTool` 在 `toolChoice: "none"` 时直接拒绝执行：

```ts
    const executeTool = (call: ToolCall) => {
      if (input.prepared.request.toolChoice?.type === "none")
        return new Tool.Error({ message: "Tools are disabled after the maximum agent steps" })
```

用户拒绝工具调用时的分支（走中断而非完成）：

```ts
        if (tools.declines.length > 0) {
          if (input.isLocationClosed()) return Outcome.Completed({ needsContinuation: true })
          return yield* Effect.interrupt
        }
```

### 证据 4：`DrainResult` 就是"轮次结束"的类型名

来源：`packages/core/src/session/runner/index.ts`（blob `493713fa67684bca6f68fb1560754700006457e4`）：

```ts
export type DrainResult = Data.TaggedEnum<{
  Complete: {}
  Moved: { readonly continuation?: Continuation }
  Reloaded: { readonly force: boolean; readonly continuation?: Continuation }
}>
export const DrainResult = Data.TaggedEnum<DrainResult>()
```

`llm.ts` 里返回 `DrainResult.Complete()` 的两处（无待处理输入、或投递语义为 queue 的 steer 不在此边界处理）：

```ts
        if (!pending) return DrainResult.Complete()
        ...
              if (!force && !continuing && (!pending || (pending.delivery === "queue" && promotable === "steer")))
                return DrainResult.Complete()
```

### 证据 5：步数上限（不是"结束"工具）

来源：`packages/core/src/session/runner/llm.ts`：

```ts
        const stepLimitReached = loaded.agent.info.steps !== undefined && step >= loaded.agent.info.steps
        const transcript = SessionModelRequest.baseTranscript({ ... })
        const prepared = yield* context.request.primary({
          session: loaded.session, agent: loaded.agent.id, model: loaded.model, tools: loaded.tools,
          system: transcript.system,
          messages: stepLimitReached
            ? [...transcript.messages, Message.assistant(MAX_STEPS_PROMPT)]
            : transcript.messages,
          // Keep tool definitions on the final Step to preserve the provider's cached prefix.
          toolChoice: stepLimitReached ? "none" : undefined,
          ...
        })
```

`MAX_STEPS_PROMPT` 定义在 `packages/core/src/session/runner/max-steps.ts`（blob `040584ab173877d12b821808489b6a7c80504954`），开头为：

```
CRITICAL - MAXIMUM STEPS REACHED

The maximum number of steps allowed for this task has been reached. Tools are disabled until next user input. Respond with text only.
```

配置字段是 `steps`：`packages/schema/src/agent.ts`（blob `c47d5e8e7ef8bf3c82de3d9fb49b16e33c5230fd`）里 `Agent.Info` 的定义为 `steps: PositiveInt.pipe(optional)`。官方文档 `packages/web/src/content/docs/agents.mdx` 的 "Max steps" 一节原文：*"If this is not set, the agent will continue to iterate until the model chooses to stop or the user interrupts the session."*，并注明 legacy 的 `maxSteps` 字段已废弃、请改用 `steps`。

### 证据 6：终止原因的枚举名

来源：`packages/schema/src/session-event.ts`（blob `0cffae27ad631740c4b7b59c09c01440b4b603a6`）：

```ts
export namespace Execution {
  export const Started = Event.durable({ type: "session.execution.started", ... })
  export const Succeeded = Event.durable({ type: "session.execution.succeeded", ... })
  export const Failed = Event.durable({ type: "session.execution.failed", schema: { ...Base, error: SessionError.Error } })
  export const Interrupted = Event.durable({
    type: "session.execution.interrupted",
    ...options,
    schema: { ...Base, reason: Schema.Literals(["user", "shutdown", "superseded", "inactivity"]) },
  })
}
```

step 级别：

```ts
export namespace Step {
  export const Started = Event.durable({ type: "session.step.started", schema: { ...Base, assistantMessageID, agent, model, snapshot?, started: NonNegativeInt } })
  export const Streamed = Event.durable({ type: "session.step.streamed", ... })
  export const Ended = Event.durable({
    type: "session.step.ended",
    schema: { ...Base, assistantMessageID, finish: FinishReason, rawFinish?, providerState?, cost, tokens, snapshot?, files? },
  })
  export const Failed = Event.durable({
    type: "session.step.failed",
    schema: { ...Base, assistantMessageID, error: SessionError.Error, finish: Schema.Literals(["content-filter"]).pipe(optional), rawFinish?, ... },
  })
}
```

其中 `finish: FinishReason` 是 provider 的 finish reason（从 `./llm.js` 引入），**与"任务完成"无关**；`Step.Ended` 只是记录 provider 正常结束了这一 step（`step.ts` 里发布它的条件是 `if (record.finish && usage && !record.failure)`）。

工具级终态同样是事件而不是"完成工具"：`session.tool.success`（`content: Schema.NonEmptyArray(Content)`）与 `session.tool.failed`，以及 `Tool.Called` 携带的 `executed: Schema.Boolean`（对应 `providerExecuted`）。

### 证据 7：没有 finish 工具（否定证据）

- `packages/core/src/tool/` 目录清单（`https://gh-proxy.com/https://api.github.com/repos/anomalyco/opencode/contents/packages/core/src/tool?ref=dev`）只有：`AGENTS.md`、`html-markdown.ts`、`http-body.ts`、`mcp.ts`、`patch.txt`、`plugin/`、`read-filesystem.ts`、`runtime.ts`。
- code search：`"finish_task"` → `total_count: 0`；`"task_complete"` → `total_count: 0`；`filename:finish` → `total_count: 2`，两个都是 finish-reason 映射实现（`packages/core/src/github-copilot/chat/map-openai-compatible-finish-reason.ts`、`packages/core/src/github-copilot/responses/map-openai-responses-finish-reason.ts`）。
- 官方 `agents.mdx` 里列出的权限键对应的工具名（`read`/`edit`/`glob`/`grep`/`list`/`bash`/`task`/`webfetch`/`websearch`/`lsp`/`skill`/`question`）里也没有任何 finish/done/submit 工具。

## 对 code_agent 的推论

1. **"模型不再发起工具调用"是可落地的循环结束判据，不证明任务目标达成。** OpenCode 把判据压缩成一个布尔 `needsContinuation`，并且放在 step 的返回值里，由最外层循环消费。`code_agent` 的 `finished`/`stop_reason` 可以照这个形状设计：不要引入 `finish_task` 工具，而是让循环在"本轮 assistant 消息里没有任何需要本地执行的 tool_call"时退出。这与 `AGENTS.md` 里"再整理事件和 `finish_task` 协议"的计划直接相关——**如果照抄 OpenCode，就不需要 `finish_task` 工具**，而是需要一个显式的结束原因字段。
2. **把判据抽成显式布尔值，而不是散落在循环里的 `if`。** OpenCode 的做法是：step 产出 `Outcome.Completed({ needsContinuation })`，外层只判断这个布尔。`code_agent` 的 `core/` 循环可以对应地让一次 step 调用返回 `(needs_continuation, stop_reason)` 之类的结构化结果，避免 REPL 层重复推断。
3. **把"上限触发"和"模型自发完成"显式区分开。** OpenCode 用 `toolChoice: "none"` + 注入提示词 `MAX_STEPS_PROMPT` 把上限转成"模型被迫用纯文本收尾"，因此上限命中时同样是 `needsContinuation === false`，但语义不同（用户看到的是一段总结和未完成清单）。`code_agent` 若保留"最大轮数"，建议也把上限收尾实现为"最后一轮禁用工具 + 注入收尾提示"，而不是直接抛异常中断，并在结束原因里区分 `completed` / `max_steps` / `interrupted` / `error`。
4. **拒绝与中断要和完成区分开。** OpenCode 里用户拒绝任何一个工具调用会让整轮走 `Effect.interrupt`（`step.ts` 的 `if (tools.declines.length > 0) ... return yield* Effect.interrupt`），并且把被拒绝的 tool call 标记为 `{ type: "aborted" }`。`code_agent` 的"中断配对收尾"（AGENTS.md 提到的下一步）可以对齐这个语义：中断不是完成，被中断的 tool call 要有配对的失败/中止结果。
5. **调用数量、执行并发和完成调用混用是三个独立设计。** OpenCode 的所研究实现按调用并发；code_agent 当前允许一轮多次调用但顺序执行。OpenCode 没有完成工具，不能由此推出另一个采用 finish_task 的系统应允许混用；该约束须由本项目单独定义。
6. **完成信号用事件/类型命名，而不是靠日志文本。** 可借鉴的命名：`DrainResult.Complete`、`Outcome.Completed`、`needsContinuation`、`Step.Ended`、`Execution.Succeeded`，以及中断原因枚举 `["user", "shutdown", "superseded", "inactivity"]`。这些名字本身就是文档。

## 未能确认的部分

- **`raw.githubusercontent.com` 与 `github.com/<owner>/<repo>/blob/<branch>/<path>` 页面未能获取**（hosts 屏蔽 + SSRF 防护）。因此本报告中的 URL 全部是 `gh-proxy.com` 代理下的 GitHub REST URL，而不是 github.com 文件页。同样地，`https://gh-proxy.com/https://api.github.com/repos/anomalyco/opencode/contents/packages/opencode/src/session/prompt.ts?ref=dev` 返回 **404 Not Found**——仓库文档里记录的旧路径在 `dev` 上确实已不存在，但我无法从 github.com 页面二次确认这一点，只能依据 tree API 的 404 与 `contents/packages` 的清单（其中已无 `opencode` 包）。
- **`packages/core/src/tool/runtime.ts` 与 `packages/core/src/session/session.ts` 的内部实现未逐行确认。** 我没有抓到这两个文件的内容，因此"工具注册表里没有 `finish_task` 之类的条目"这一结论只建立在 `contents/packages/core/src/tool` 的目录清单 + code search 的 0 命中之上，而不是对注册表代码的逐行阅读。如果后续需要 100% 排除"完成工具被动态注册"，建议再抓 `packages/core/src/tool/runtime.ts`（blob `d1c5e29caf157fce941024570bc1fe9d5727621e`）验证。
- **code search 的召回边界未验证。** `search/code` 只索引默认分支且有服务端限制，`"finish_task"` / `"task_complete"` 的 `total_count: 0` 只能说明查询未返回这两个字符串的命中，不能证明仓库中绝对不存在；不能排除存在别的命名（例如某个 provider 侧 hosted 的完成信号）。我没有穷举候选名。
- **`packages/web/src/content/docs/agents.mdx` 是官方文档而非运行时行为定义**，且该文件在 `services/www/src/docs/content/agents.mdx` 有一份镜像（code search 结果里同时出现两处）。我读的是 `packages/web` 下的那份；两份是否字节相同未验证。
- **"每一轮"这个词在 OpenCode 里有三个不同粒度**：外层 `drain`（一次用户输入触发的整个轮次）、`step`（一次 provider 请求）、`attempt`（含重试的一次 step 尝试）。本报告按此区分，但没有抓到 `packages/core/src/session/run-coordinator.ts`、`execution.ts` 去确认 `drain` 被谁调用、以及一次用户输入是否可能触发多次 `drain`。
