# Agent 运行记录的持久化时机调研

调研日期：2026-10-27。一手来源为官方文档和项目源码；`main` 链接是本次访问的可变快照，不代表固定版本。OpenAI Agents SDK 的循环另外核查了固定标签 `v0.22.2`。本次只调研，不修改 M5a 接口和测试。

## 结论

用户对“整个任务结束才统一写文件”的质疑成立：它保留不了进程突然退出前的中间过程。核查的系统存在按完整消息、按执行步保存，以及显式选择退出时保存的不同方案，不能概括为全部逐 token 落盘。消息持久化、恢复检查点和可观测 tracing 也有不同目的与耐久性。

## 1. Pi coding agent：完整消息结束后写 JSONL

`AgentSession._handleAgentEvent` 在 `message_end` 时，将 user、assistant、toolResult 等消息交给 `SessionManager.appendMessage`；`agent_end` 不是统一保存所有消息的触发点。`subscribe` 的注释也明确消息在 `message_end` 保存。[官方 agent-session.ts，约 1083–1102、1307–1310 行](https://raw.githubusercontent.com/badlogic/pi-mono/main/packages/coding-agent/src/core/agent-session.ts)

`appendMessage → _appendEntry → _persist` 同步写文件：新会话出现 user 或 assistant 消息后才创建 JSONL；首次写已有条目，此后每条使用 `appendFileSync`。源码注释特别说明从 user 消息就开始写，是为了首轮未完成时仍保留用户输入。[官方 session-manager.ts，约 1080–1128 行](https://raw.githubusercontent.com/badlogic/pi-mono/main/packages/coding-agent/src/core/session-manager.ts)

这支持“每条完整消息提交后追加”，没有支持“每个流式字符都写文件”的证据。该追加路径未调用显式 fsync，因此同步文件 API 返回也不能直接解释为断电安全。

## 2. LangGraph：执行步检查点与耐久性策略

官方 checkpointer 文档将完整状态快照放在 super-step 边界；一个顺序节点执行对应一个步。节点完成时另有 task-level pending writes，供同一步中其他节点失败后的恢复使用。这里的恢复边界是执行步，而非任意一个 token。[官方 Checkpointers：Super-steps](https://docs.langchain.com/oss/python/langgraph/checkpointers#super-steps)

持久化时机可选：`sync` 在进入下一步前等保存完成；`async` 在下一步执行期间异步保存，进程崩溃可能留下未保存窗口；`exit` 只在执行退出时保存，官方明确运行中进程崩溃不能恢复中间状态。这说明结束统一写入确实也是一种性能取舍，但不满足本次希望保留崩溃前轨迹的目标。[官方 Durability modes](https://docs.langchain.com/oss/python/langgraph/checkpointers#durability-modes)、[Durability 类型参考](https://reference.langchain.com/python/langgraph/types/Durability)

真正跨进程留存还要求持久后端：`InMemorySaver` 只放 RAM；官方列出文件 SQLite 和 PostgreSQL 等后端。[官方 Persistence：MemorySaver does not persist between restarts](https://docs.langchain.com/oss/python/langgraph/persistence#memorysaver-does-not-persist-between-restarts)

## 3. OpenAI Agents Python SDK：Session 按 turn 保存，tracing 另外批量导出

Session 概览的高层说明仍概括为 run 前读取、run 后保存；精确时机需继续看实现。固定标签 `v0.22.2` 的非流式循环在 `NextStepRunAgain` 分支先 `await save_turn_items_if_needed(...)` 再 `continue`，所以不会等整个多轮工具任务最终返回才保存。[官方 Sessions 概览](https://openai.github.io/openai-agents-python/sessions/)、[v0.22.2 run.py，约 2052–2063 行](https://raw.githubusercontent.com/openai/openai-agents-python/v0.22.2/src/agents/run.py)

当前维护参考进一步明确每个已完成 turn 都应持久化，使工具输出和 handoff 项能跨越后续错误/中断保留；已保存计数用于避免流式重试或恢复时重复写入。这是完整语义项/turn 的提交策略，不是每个原始分片都存储。[官方 Session Persistence 维护参考](https://github.com/openai/openai-agents-python/blob/main/.agents/references/session-persistence.md)、[官方 helper 源码](https://raw.githubusercontent.com/openai/openai-agents-python/main/src/agents/run_internal/agent_runner_helpers.py)

当前 `SQLiteSession.add_items` 会插入本批 items 并 `conn.commit()`；默认 db_path 却是 `:memory:`，要指定文件路径才有跨进程保存。[官方 SQLiteSession，约 16–21、414–429 行](https://raw.githubusercontent.com/openai/openai-agents-python/main/src/agents/memory/sqlite_session.py)

tracing 使用另一条路径：默认 `BatchTraceProcessor` 在 span 结束后放内存队列，由后台线程批量导出；默认最多 128 项一批、5 秒检查间隔，另有队列阈值触发和 `force_flush`。队列满或导出失败可能丢弃，因此这套遥测本身不是崩溃恢复日志。[官方 tracing 架构](https://openai.github.io/openai-agents-python/tracing/#custom-tracing-processors)、[官方 BatchTraceProcessor 参考及源码](https://openai.github.io/openai-agents-python/ref/tracing/processors/#agents.tracing.processors.BatchTraceProcessor)

## 4. 追加、flush、耐久性分别解决什么

对 Python 缓冲文件，`write` 可能只进入进程缓冲；`flush()` 推出语言运行时缓冲；如要求调用系统同步文件到磁盘，官方用法是先 `f.flush()` 再 `os.fsync(f.fileno())`，Windows 对应 `_commit()`。[Python 官方 os.fsync](https://docs.python.org/3.11/library/os.html#os.fsync)

数据库也要说明故障模型：SQLite WAL + synchronous=NORMAL 的已提交事务能跨应用进程崩溃保留，但系统崩溃或断电可能回滚最近事务；FULL 增加每次提交的 WAL 同步。不能把“执行了 commit”一概称为所有故障下绝不丢失。[SQLite 官方 synchronous](https://sqlite.org/pragma.html#pragma_synchronous)

## 5. 已纳入 M5a 规格的设计推论（实现与验收另行确认）

1. 规格将已提交消息的记录时机设为“每条消息经 History 校验并提交后立即追加”，包括 user、完整 assistant、tool 结果及取消收尾的补写观察。完整助手调用应在执行工具前保存；工具结果在取得并提交后保存。临时 TextDelta 可继续只显示。
2. 轨迹写入采用独立接口，由 core 连接 History 提交边界和 writer。写入失败必须阻止后续模型请求和工具执行，并在禁用失败回调后结清历史；这是一项会影响执行顺序的契约。当前 UI 事件既没有用户消息提交，也没有取消收尾的所有观察，不能单独承担完整消息轨迹。
3. 每条追加后 flush，明确第一版主要应对应用进程突然终止；若要承诺更强耐久性，再选定并验证 fsync 策略。JSONL 只是格式，不自动提供这种保证。
4. 正常结束、可处理错误和取消时追加 end；强杀进程可能没有 end。只读回放可显示有效前缀并标明未结束，截断末行与中间损坏要区分，不能把未结束文件一律拒绝后连已保存过程也看不到。
5. 添加强杀独立子进程的实验：确认工具执行前已保存调用；杀进程后仍能回放已写消息并发现缺 end。该实验只证明轨迹留存，M5b 再设计从何处恢复。
6. 落盘调用后、工具结果保存前仍可能崩溃，无法仅凭日志断定副作用是否发生。此时保持状态未知，不自动重跑命令；这条边界需要幂等性或外部状态核查解决，频繁保存本身不能实现副作用恰好执行一次。

调研时的 M5a 规格采用“run 结束后统一导出”和“无 end 就全部拒绝”；这些设计已在后续规格中修订，本次调研不代表实现或验收。

## 6. 会话文件的生命周期与终态记录（补充）

- Pi 将同一会话的消息写入同一份 JSONL；新会话首次有对话消息时创建文件，后续条目使用 `appendFileSync` 追加。它在消息完成时持久化，而不是一直持有一个打开的流；同步调用也不等于保证已经 `fsync`。[消息处理源码](https://raw.githubusercontent.com/badlogic/pi-mono/main/packages/coding-agent/src/core/agent-session.ts)、[SessionManager 源码](https://raw.githubusercontent.com/badlogic/pi-mono/main/packages/coding-agent/src/core/session-manager.ts)。以上为本次访问的 `main` 快照。
- OpenHands SDK 的 `EventLog` 按事件追加存储：在锁内经 FileStore 写入各自的事件文件，本地文本存储使用原子写辅助函数，辅助函数中通过 `with` 管理临时文件。它采用逐事件文件，不是证据表明所有 Agent 都使用 JSONL 或长持有流。[EventLog 源码](https://raw.githubusercontent.com/OpenHands/software-agent-sdk/main/openhands-sdk/openhands/sdk/conversation/event_store.py)、[本地文件存储](https://raw.githubusercontent.com/OpenHands/software-agent-sdk/main/openhands-sdk/openhands/sdk/io/local.py)、[原子写函数](https://raw.githubusercontent.com/OpenHands/software-agent-sdk/main/openhands-sdk/openhands/sdk/utils/files.py)。
- 项目建议：一个会话可用 `with open(...)` 持有流并在每条记录后 `flush()`，由 `with` 完成正常退出与可处理异常时的关闭，因此不一定需要公开 `close()`。`with` 关闭资源，不会自动产生一次 `Agent.run` 的结果；如果要区分完成、失败、取消及异常中断，仍须追加相应终态数据，不必限定方法名为 `write_end()`。
- 调研时的 M5a 规格和测试为一次运行一份文件，end 最多一次且必须最后；改成一个会话一份文件时，同一文件可能包含多次运行的终态，不能沿用原先的 `complete` 含义和解析规则。后续已同步运行分段和回放判据；本次调研不覆盖用户的练习代码。强杀可能跳过上下文清理，逐条 flush 后仍要允许回放有效前缀。
