# Codex、OpenCode 与 ZCode 的会话持久化调研

调研日期：2026-10-27。聚焦会话与运行的组织、消息持久化的时间和资源生命周期。源码与文档是静态调查，不等于本机强杀或断电测试；不同产品、版本和后端的保证可能不同。访问可变分支时明确注明，不推断所有版本行为。

## Codex：官方文档能确认的边界

- App Server 把 thread 作为持续会话，`thread/resume` 之后可继续提交 turn；每次 turn 有 started/completed 事件，结束状态包含 completed、interrupted、failed。`thread/items/list` 查询已保存的 item；thread 的持久 JSONL 日志可归档。`--ephemeral` 可以不写会话 rollout 文件。[App Server 官方文档](https://learn.chatgpt.com/docs/app-server)、[CLI 命令参考](https://learn.chatgpt.com/docs/developer-commands)。
- `codex exec --json` 将运行事件以 JSONL **输出到 stdout**，事件包括 thread、turn、item 的开始和完成。这是流式输出协议；不能仅凭 stdout 格式推断内部线程日志如何缓冲、何时 flush 或能否跨崩溃保留。[非交互模式文档](https://learn.chatgpt.com/docs/non-interactive-mode)。
- 本地状态另有可配置的 `history.jsonl`；排障文档另列出 `$CODEX_HOME/sessions` 会话记录目录。不要把 `history.jsonl`、thread rollout、stdout JSONL 和遥测日志混为一个文件。[配置说明](https://learn.chatgpt.com/docs/config-file/config-advanced)、[排障文档](https://learn.chatgpt.com/docs/reference/troubleshooting)。
- 官方文档中 OTEL 导出是后台批处理并在关闭时 flush，它是观测通路，不能据此推断会话恢复记录的写入时机。[高级配置](https://learn.chatgpt.com/docs/config-file/config-advanced)。
- 文档足以说明会话、运行和状态分别存在，尚不足以确认每条消息是否同步写盘、工具执行前后具体持久化顺序、文件句柄生命周期或断电保证；这些留待可核验的实现证据，不能据事件已发出推断其已持久化。

## OpenCode：SQLite 与事件投影

官方说明项目、会话和消息保存于 `opencode.db`，日志和撤销快照是独立数据。以下源码固定在 `v2.0.28`，是静态阅读，不代表每一发行版或部署方式。[官方存储说明](https://docs.opencode.ai/docs/troubleshooting/)。

- 事件模型区分临时和可持久化的事件：`Text.Delta` 用于实时流，`Text.Ended` 带完整文本并标为 durable；工具输入增量和进度是临时事件，调用、成功、失败是 durable 事件。可见文本不必等整个多步任务结束，但不意味着每个 token 均已写盘。[事件定义](https://raw.githubusercontent.com/anomalyco/opencode/v2.0.28/packages/schema/src/session-event.ts)、[流事件发布](https://raw.githubusercontent.com/anomalyco/opencode/v2.0.28/packages/core/src/session/runner/publish-llm-event.ts)。
- durable 事件在 SQLite 事务中运行 projector、更新会话序号；projector 将消息及工具状态投影到会话消息表。只有 bus 的 `persist` 选项为真才额外写入事件表，源码默认值为 false；具体产品部署如何配置此选项本次未核实，不能断言每个部署都保留完整事件表。[Bus 事务](https://raw.githubusercontent.com/anomalyco/opencode/v2.0.28/packages/core/src/bus.ts)、[消息投影](https://raw.githubusercontent.com/anomalyco/opencode/v2.0.28/packages/core/src/session/projector.ts)。
- 支持相关参数的 SQLite 后端配置 WAL 和 `synchronous=NORMAL`；SQLite 官方说明这能让已提交事务跨应用进程崩溃保留，但突然断电或系统崩溃时最近事务仍可能回滚。它不是每个会话持有一个 JSONL 写入流。[数据库配置](https://raw.githubusercontent.com/anomalyco/opencode/v2.0.28/packages/core/src/database/database.ts)、[SQLite synchronous](https://sqlite.org/pragma.html#pragma_synchronous)。

## ZCode：智谱 Z.ai 的实现与协议

此处假定用户所指是智谱 [ZCode](https://github.com/zai-org/ZCode)；存在其他同名项目。以下按该项目本次访问的 `main` 文档，不能推断每个发行版本及不同部署均相同。

- 独立 CLI 默认会话数据库是 `~/.zcode/cli/db/db.sqlite`；桌面、远端和 Web 的数据位置可能不同。官方同时说明模型 I/O 调试日志是另一条数据路径，不要混同会话数据库。[官方 NOTICE](https://github.com/zai-org/ZCode/blob/main/NOTICE.md)。
- 开源 CLI 的 `SqliteSessionStore` 持有 SQLite 连接，并提供 `close()`；`saveMessage` 和 `savePart` 分别交给 repository，按 ID 插入或更新、保留相应顺序。它不是每次重写整份会话 JSON 文件。[存储实现](https://raw.githubusercontent.com/zai-org/ZCode/main/apps/zcode-cli/packages/adapters/src/storage/session-store/sqlite-session-store.ts)、[消息与片段 repository](https://raw.githubusercontent.com/zai-org/ZCode/main/apps/zcode-cli/packages/adapters/src/storage/session-store/repositories/messages.ts)。
- 模型步骤的取消分支会保存当时已有的流式快照；这是进程仍能处理取消的路径，不能推断强杀进程后最后的文本也已保存。源码还包含流式工具协调，不能概括为其全部工具都在整轮响应结束后执行。[模型步骤](https://raw.githubusercontent.com/zai-org/ZCode/main/apps/zcode-cli/packages/core/src/runtime/methods/turn-model-step.ts)。
- V4 协议以 CLI 为 conversation 的权威来源，界面读取 `ConversationSnapshot` 和后续 delta；渲染层不维护第二份权威消息和队列。V4 快照和 delta 是客户端同步协议，不能仅从该协议推断每个 delta 已经写盘。[官方协议](https://github.com/zai-org/ZCode/blob/main/docs/zcode-protocol.md)。
- 协议描述输入执行前写入 `session_input` 生命周期账本。账本支持输入提升的原子性和重启查重，但不是重启后自动执行的队列：CLI 重启时已接收但尚未提升的输入变为 discarded，由客户端提示用户确认重发。这是输入队列边界，不等于所有正在运行的工具都能被确定为未执行。[协议队列语义](https://github.com/zai-org/ZCode/blob/main/docs/zcode-protocol.md#队列语义)。

## 对本项目的启发（设计建议，不是这些工具的统一标准）

1. 会话可拥有一份可追加的 JSONL，用于记录多次运行；每次运行有可识别的开始与结束，运行结束不关闭整个会话。完整消息与工具观察应及时写入，临时流式片段可只供显示。
2. 一个会话使用 `with open(...)` 管理流，并在写入每条记录后 `flush()` 是可行的项目设计；不必暴露单独 `close()`。终态可通过统一的写记录方法追加，不必专设 `write_end()`。`with` 不提供强杀时的清理保证，`flush` 不承诺断电安全。
3. 落盘后中断时，回放完整记录前缀，标明尚未结束的运行和无结果的工具调用；不能从缺结果推断未执行，也不能自动重跑。工具副作用与本地记录不能组成原子事务。
4. 现行 [M5a 规格](../design/m5a-tracing.md) 已采用会话文件、多运行关联与有效前缀回放。首次练习不自动接入默认目录，由调用方提供文件流；后续默认存储采用用户目录并记录工作目录关联。规格确定不等于当前实现或验收完成。
5. 工具调用记录写入成功后才执行是本项目选定的顺序；记录失败也必须结清内存历史。它缩小证据丢失窗口，不能消除“副作用已发生、结果未落盘”的窗口，更不提供恰好执行一次。
