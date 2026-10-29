# Agent 工具返回格式研究

研究日期：2026-10-08。范围：Codex 公开 Rust 实现、OpenCode 开发分支、Claude Code 官方公开契约、Anthropic 工具协议与 MCP 标准。源码分支与在线文档会变化；这里记录查阅时的行为，不将开发分支等同于所有已发布版本。Claude Code 的公开契约不能证明其全部内部实现。

## 结论

建议 code_agent 定义轻量的统一工具结果。统一的是工具交给执行器的接口；工具自身的领域数据、模型消息、界面事件和日志记录仍各有职责。这是对下述证据的设计归纳，不是三家共享同一个标准类型。

## Codex：统一输出接口，多种具体输出

公开源码的 `ToolOutput` trait 分别提供日志表示 `log_output`、模型消息转换 `to_response_item`、Code Mode 的程序结果 `code_mode_result`；还能取得仅供主机使用的 metadata。它说明同一执行结果可以有不同用途的表示，日志也不保证完整。来源：[ToolOutput](https://github.com/openai/codex/blob/main/codex-rs/tools/src/tool_output.rs)。

具体实现各有字段：`FunctionToolOutput` 包含内容块和可选成功标记；`ExecCommandToolOutput` 保存输出字节、退出码、运行进程及截断策略，生成模型可读的文本；`McpToolOutput` 包装 MCP 结果。来源：[具体输出实现](https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/context.rs)。

另一条 shell 执行路径的 `ExecToolCallOutput` 有 stdout、stderr、合并输出、退出码、耗时和超时标记。来源：[执行结果类型](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/exec_output.rs)。格式化器把退出码、耗时、超时提示和截断输出整理为模型文本。来源：[格式化器，固定源码快照](https://github.com/openai/codex/blob/78245b47af2a7aafcabe025828ceecca69db4df1/codex-rs/core/src/tools/mod.rs#L96)。

可反馈给模型的调用错误与致命错误分别表示为 `RespondToModel`、`Fatal`。来源：[FunctionCallError](https://github.com/openai/codex/blob/main/codex-rs/tools/src/function_call_error.rs)。这不等于每个命令的非零退出都要变成致命错误。

## OpenAI API：传输格式与内部类型分别设计

普通 function calling 的结果通常是字符串，其内容可自行选择文本或 JSON；Chat Completions 通过 `role=tool`、`tool_call_id` 与请求对应。来源：[官方 function calling 文档](https://developers.openai.com/api/docs/guides/function-calling)。不能把 Codex 内部成功字段当成所有模型供应商支持的通用协议字段。

专用 Responses shell 协议另有 `shell_call_output`，里面包含 stdout、stderr 与退出或超时 outcome，官方要求保留非零退出输出供模型恢复。来源：[官方 shell 文档](https://developers.openai.com/api/docs/guides/tools-shell)。这是特定协议，不意味着本项目的 OpenAI SDK / DeepSeek Chat Completions 接入应切换 API。

## OpenCode：执行结果、执行状态、模型消息分层

研究日期：2026-10-08。下列源码来自官方仓库的 `dev` 分支，属于可变开发快照，不代表所有发布版本。当前目录使用 `shell.ts`，旧路径 `bash.ts` 已不可读取。

内置工具共用 `ExecuteResult<M>`：`title: string`、`output: string`、工具自定义的 `metadata: M`、可选 `attachments`。`callID` 在执行上下文；成功返回值没有自行填写调用 ID 或生命周期状态。包装层统一验证参数；若工具未自行处理截断，则截断 `output`，并增加 `metadata.truncated` 和可选 `outputPath`。[官方 tool.ts](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/tool/tool.ts)

插件接口更宽松：handler 可返回字符串，也可返回 `{output, title?, metadata?, attachments?}`，说明对扩展作者的接口可以比内部归一化结构更简单。[官方插件 ToolResult](https://github.com/anomalyco/opencode/blob/dev/packages/plugin/src/tool.ts)

执行记录独立保存调用关联及状态：processor 创建 `callID`、`tool` 与 `pending` 状态；成功结果写入 `completed`，包含 input/output/title/metadata/attachments/time；工具错误写入 `error` 状态和错误信息。这是生命周期记录，不是每个工具 payload 都重复维护的字段。[官方 processor.ts](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/session/processor.ts)

Shell 的特有事实在 metadata：`exit`、输出预览、`truncated`、`outputPath`。读取时限制缓存并将大输出保存到文件；模型文本中也写入截断提示及文件位置，超时/取消提示进入文本。它在正常进程退出后直接返回 exit code，没有因非零码而抛出错误的判断。结合 processor 对正常返回的处理，可以推导：单纯非零退出会形成 completed 结果及 metadata.exit；这个结论是源码分析，未实测运行。[官方 shell.ts](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/tool/shell.ts)

模型回灌另做投影：completed 状态主要取 output 文本及适用的 attachments，再携带 `toolCallId`；不会直接将整个 metadata 对象序列化给模型。error 状态通常成为 output-error，并保留调用关联。媒体还会按模型供应商能力调整。[官方 message-v2.ts](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/session/message-v2.ts)

**可借鉴的结构（研究推论）**：统一轻量外壳，保留各工具 metadata；状态/调用 ID 属于执行记录；模型只看经过选择的内容。因此退出码、超时等会影响模型决策的事实，不能只留在模型看不到的 metadata。

## Claude：区分 Claude Code、Agent SDK 和 Messages API

Claude Code 公开 Hooks 契约提供了有用的边界证据：`PostToolUse` 收到各工具的结构化 Output，`tool_response` 的 schema 因工具而异；例如 Write 是 filePath/type，Bash 示例是 stdout/stderr/interrupted/isImage。调用 ID、执行时长和工具名在 hook 事件外围。`PostToolBatch` 收到的则是模型看到的序列化 tool_result 内容。成功与失败还有不同 hook，失败事件提供 error/is_interrupt。以上能证实公开接口分层；无法据此证明全部闭源内部类型或截断策略。[官方 Hooks reference](https://code.claude.com/docs/en/hooks#posttooluse-input)、[PostToolBatch](https://code.claude.com/docs/en/hooks#posttoolbatch)

Agent SDK 自定义工具走进程内 MCP server，返回 `content` 内容块数组及可选错误信号，TypeScript 支持可选 `structuredContent`。当前文档特别说明：TS 有 structuredContent 时，转发 JSON 及非文本内容，content 的 text 被视为重复数据而不再转发；Python `@tool` 只转发 content/is_error，需要独立 MCP server 才能返回 structuredContent。这里是语言实现差异，不应把一个 SDK 的行为当成所有 Claude 工具的统一约束。[官方 custom-tools 文档](https://code.claude.com/docs/en/agent-sdk/custom-tools)

模型协议层的 client tool 结果是 user message 内的 `{type: "tool_result", tool_use_id, content?, is_error?}`。content 可以是字符串或规定的内容块；tool_use_id 对应模型生成的 tool_use.id；is_error 表示工具执行错误。每个 client tool_use 都应有对应结果，位置顺序也有要求。这个协议规定回灌封套，并不要求本地所有工具的业务数据都有相同 JSON 字段。[官方 Handle tool calls](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls)

**可借鉴的结构（研究推论）**：内部可保留工具专属结构，再转换为模型协议；call ID 是协议关联；需要向模型传达的错误包含可操作信息。不要把 Claude Agent SDK 的最终 agent ResultMessage，与一次工具结果混为一谈。

## MCP：互操作协议的对照

本节固定采用 MCP `2025-11-25` 规范。工具结果 `content` 支持文本、图像、音频、资源链接与内嵌资源；可选 `structuredContent` 是结构化 JSON 对象，可用 outputSchema 约束。规范为兼容旧客户端建议同时用文本块提供 JSON。普通同步调用的关联 ID 在 JSON-RPC 封套，不在每个结果 payload 内。[官方 Tools 规范](https://modelcontextprotocol.io/specification/2025-11-25/server/tools#tool-result)

规范区分 JSON-RPC 协议错误与 `isError: true` 工具执行错误；后者仍是正常 result，含模型可读取的失败原因，以便纠正参数重试。核心 CallToolResult 没有为所有工具规定 exit_code、stdout 或 truncated。[官方 Error Handling](https://modelcontextprotocol.io/specification/2025-11-25/server/tools#error-handling)

**可借鉴的结构（研究推论）**：统一结果外壳很常见；具体领域数据、模型内容、调用记录应分开。项目目前只做文本工具，无需立即搬入完整 MCP 多模态内容模型，未来通过适配器接 MCP 即可。

## 对 code_agent 的决定与本步实现

用户于 2026-10-08 确认统一结果仅含三个字段：output、status、truncated。此前的 output/is_error/truncated/metadata 四字段提案不再采用。上文对行业实现的研究仍作为比较依据，不代表项目必须照搬其字段。

```python
from enum import StrEnum

from pydantic import BaseModel, JsonValue


class ToolStatus(StrEnum):
    COMPLETED = "completed"
    ERROR = "error"
    TIMED_OUT = "timed_out"


class ToolResult(BaseModel):
    output: JsonValue
    status: ToolStatus
    truncated: bool = False
```

当前 [events.py](../../src/code_agent/events.py) 保留该三字段结构；Registry、Agent 与默认 Bash 路径已接通统一结果。调用关联位于消息与事件外围，工具的领域数据保存在 output。当前行为及验收状态以 [PLAN.md](../../PLAN.md) 为准。

| 字段 | 约定 |
| --- | --- |
| output | 工具观察与失败原因，可为字符串或结构化 JSON；回灌时转换为模型消息文本 |
| status | 调用结束类别，不表示用户任务是否完成 |
| truncated | 输出发生信息丢失；模型文本也要明确说明 |

Bash 的 output 采用结构化 JSON，包含 stdout、stderr 和原始 exit_code。正常进程退出(含非零)为 COMPLETED；超时为 TIMED_OUT，保留部分输出与原因，exit_code 为 null。无法启动等执行错误为 ERROR，原因放在 output 中。截断标记放在 ToolResult.truncated，工具调用状态放在 ToolResult.status，无需在 output 中重复保存相同标记。

Agent 已按以下形式将结果序列化到配对的工具消息：

```python
{"role": "tool", "tool_call_id": call.id, "content": result.model_dump_json()}
```

这次选择省去 metadata，程序以后确实需要直接读取工具专属字段时再讨论结构化数据接口。通用 core 不应按工具名到处解析 output。回灌文本应包含 output、status 和 truncated，让模型同时得到状态与完整性信息；不能直接把 ToolResult 或字典放进当前 Chat Completions 的 content。

Bash 在读取阶段实施 stdout/stderr 合计 20,000 字符的保留预算，超额后仍排空管道；不会通过截断序列化后的 JSON 实施预算。取消完成清理后传播；核心另行结清 pending 调用。Windows Git Bash 还使用定向控制台信号，实际范围见[取消修复](../design/m4b-git-bash-cancellation.md)。

2026-10-09 交接状态：[test_shell.py](../../tests/code_agent/tools/builtin/test_shell.py) 的八个清理测试通过，包括真实父子 Python 进程；两个正常执行测试因 run_bash 缺少 ToolResult 返回而失败。这是当时的未完成状态；后续已接通默认 Bash、结果回灌和预算控制，不能将该失败数量当作当前测试结果。
