# 会话存储职责调研：Pi、OpenCode 与 Codex

调研日期：2026-10-27（Asia/Shanghai）。聚焦会话 ID、文件路径、资源所有权和消息持久化接线。本文记录源码事实，不修改当前有效规格或代替实现验收；未运行这些产品、真实模型或强杀实验。

## 来源范围与既有笔记

- 已阅读 [Pi 核心设计研究](pi-coding-agent-design.md)、[Session 与 Agent 的绑定](session-agent-binding.md)、[持久化时机](agent-persistence-timing-20261027.md)和[会话持久化](coding-agents-persistence-20261027.md)。既有笔记用于定位主源，下面的职责结论另经本次官方源码核对。
- Pi 的 `AgentSession` 可核对固定提交 `be14d9732f98fcc33929cf103a92b96afaa6523e`；该提交的 `session-manager.ts`、`sdk.ts` 本次抓取失败，因此这两份使用本次访问的官方 `main`，不能把混合来源描述成完全同一版本快照。
- OpenCode 以下均固定于 `v2.0.28`；没有将旧 `dev` 路径或社区扩展当成此版本核心实现。
- Codex 使用本次访问的官方 `main`。未锁定提交 SHA，不推断所有发行版本均如此。

## Pi：会话存储封装路径，组合层连接 Agent

### ID、路径和写入资源

`SessionManager.create(cwd, sessionDir?)` 选择默认或指定目录，构造时初始化新会话；`newSession()` 生成 ID、时间和包含 `cwd` 的会话头，并确定 `<timestamp>_<id>.jsonl`。因此正常使用者传工作目录和可选存储目录，不必自己生成每个文件名。默认目录按工作目录编码分组。[SessionManager，`main`，L982–1006、L1634–1642](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/session-manager.ts#L982)

`_persist()` 在会话首次包含 user 或 assistant 消息后，以 `openSync(path, "wx")` 创建文件，写入已积累条目，并在 `finally` 中关闭描述符；后续通过 `appendFileSync(path, ...)` 追加。因此它持有会话路径和内存条目，没有为整个会话长期持有一个打开的输出流。源码没有在这条追加路径显式 `fsync`，不能据同步 API 推断断电安全。[SessionManager，`main`，L1079–1129](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/session-manager.ts#L1079)

### 持久化接线

SDK 的 `createAgentSession()` 默认创建 `SessionManager`，也接受调用方注入的 manager，然后将它与底层 Agent 一起交给 `AgentSession`。这是组合入口创建能力对象并连接的做法，具体文件打开细节留在 manager 内部。[SDK，`main`，L182–199、L430–447](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/sdk.ts#L182)

固定提交的 `AgentSession` 在 `message_end` 处理 user、assistant、system、toolResult，并调用 `sessionManager.appendMessage`；不是等整个多步任务结束才导出。其公开订阅说明也把内部持久化与外部监听区分开。各运行模式在会话对象外添加 I/O；`dispose()` 主要取消运行、断开监听、清理会话相关资源，不能解释成关闭一个长期打开的 JSONL 流。[AgentSession，固定提交，L1–13、L1083–1105、L1307–1354](https://github.com/earendil-works/pi/blob/be14d9732f98fcc33929cf103a92b96afaa6523e/packages/coding-agent/src/core/agent-session.ts#L1083)

## OpenCode：多个会话共享数据库，运行操作使用会话 ID

`Session.make()` 的注释要求在 host Scope 内构建一次；`forSession(id)` 返回绑定 ID 的操作句柄，复用 host 服务并重新读取当前状态。运行操作从 `SessionStore` 读取会话，依赖 Bus、Database、Execution 等服务。这说明会话业务对象与物理存储连接不是一会话一对应关系。[Session 操作，v2.0.28，L34–50、L363 起](https://github.com/anomalyco/opencode/blob/v2.0.28/packages/core/src/session/session.ts#L34)

会话创建的 durable 事件由 projector 写入 `SessionTable`，ID、项目、位置和元数据来自事件；消息投影保存到会话消息表。此实现是按 ID 关联的数据库结构，并非每个会话生成一份 JSONL。[Projector，v2.0.28，L426–456、L598 起](https://github.com/anomalyco/opencode/blob/v2.0.28/packages/core/src/session/projector.ts#L426)

Database 层接收数据库路径：绝对路径直接使用，相对路径关联 `Global.data`；支持的后端配置 WAL 和 `synchronous=NORMAL`。它也允许注入 SqlClient，不要求所有部署都打开本地文件。[Database，v2.0.28，L29–73、L91–109](https://github.com/anomalyco/opencode/blob/v2.0.28/packages/core/src/database/database.ts#L29)

Bun 后端在 `nativeLayer` 创建 `Database(config.filename, ...)`，注册 `Effect.addFinalizer(() => native.close())`。连接由存储层的作用域清理，与 UI 的每次输入、单次模型调用分开；这条结论特指已核查的 Bun 后端。[SQLite Bun 层，v2.0.28，L76–95](https://github.com/anomalyco/opencode/blob/v2.0.28/packages/core/src/database/sqlite.bun.ts#L76)

Bus 在事务内运行 durable event 的 projectors、更新序号并提交，然后通知监听者；额外保留完整事件 payload 到事件表取决于 `persist`，源码默认 false。不能把“业务投影已持久化”与“保留所有原始事件”混为一谈。[Bus，v2.0.28，L159–187、L293–425](https://github.com/anomalyco/opencode/blob/v2.0.28/packages/core/src/bus.ts#L293)

本次未完整追踪 v2.0.28 创建会话时生成原始 ID 的入口，不对其具体 ID 算法作结论。已核查的事实足以区分 session ID 与数据库路径。

## Codex：ThreadStore 隐藏文件与写入器

源码为本次核查的官方 `main`：

- `ThreadStore` 的职责说明将应用使用的 `ThreadId` 与具体文件/RPC 存储分开；核心 `ThreadManager` 持有 store，并安排线程身份与生命周期。[ThreadStore](https://github.com/openai/codex/blob/main/codex-rs/thread-store/src/lib.rs)、[ThreadManager](https://github.com/openai/codex/blob/main/codex-rs/core/src/thread_manager.rs)
- 本地创建路径把线程元数据和工作目录传给 RolloutRecorder；live writer 将追加、flush、shutdown 转交 recorder，并在关闭时移除对应活动 recorder。[创建](https://github.com/openai/codex/blob/main/codex-rs/thread-store/src/local/create_thread.rs)、[Live writer](https://github.com/openai/codex/blob/main/codex-rs/thread-store/src/local/live_writer.rs)
- RolloutRecorder 计算落盘位置，由后台写入任务持有文件；追加接口可只完成入队，flush 是独立等待点，shutdown 处理排空。新会话可以延迟创建实体文件。因此不能将一次 record 调用返回等同于已经持久化，也不能从 JSONL 格式推断逐条同步写入。[RolloutRecorder](https://github.com/openai/codex/blob/main/codex-rs/rollout/src/recorder.rs)

## 从源码可得的共同结论

| 问题 | 核查结论 |
|---|---|
| UI 是否应拼文件名、直接操作文件句柄？ | 核查的设计把物理存储放在核心调用的存储模块中；UI 使用会话/线程操作。 |
| 一个工作目录是否只能有一份会话记录？ | 否。会话身份独立于工作目录；Pi 按 ID 生成文件，OpenCode 在共享数据库中按 ID 关联。 |
| 是否必须把外部 stream 作为产品入口参数？ | 否。Pi 接收工作目录/可选会话目录，Codex 用线程 store；文件句柄可以是内部细节。 |
| 是否必须长期打开一个文件？ | 否。Pi 使用逐次文件 API，Codex 有后台写入器，OpenCode 有作用域内共享数据库连接。 |
| 是否必须新建一层名为 AgentSession 的 Python 类？ | 源码不能支持这个结论；职责可借鉴，类数量取决于本项目已有状态与运行入口。 |
| 已落盘是否代表副作用恰好执行一次？ | 否。会话记录与工具副作用不是一个原子事务；本次静态调查没有验证强杀或断电保证。 |

项目最终接口应据这些职责与当前 `Agent`/History 结构确定。本文不把各产品的类层级、数据库或异步写入队列自动列为 M5a 要求。

## 对 code_agent 的具体建议（项目推导，待同步有效规格）

> 本节保留调研时的候选方案。后续已按用户决定拆出 sessions/manager.py 与 store.py，采用单会话 JSONL 和操作内文件生命周期；创建时标题为 None。当前有效接口与实现范围见[会话管理规格](../design/session-management.md)，不沿用下文的工厂、跨操作文件流或 close 接口。

### 采用一个具体的会话存储模块

建议在 `tracing/session.py` 提供具体的 `SessionStore`，每个实例对应一份新会话记录。公开入口接收工作目录和存储根目录，不要求 CLI 提供文件流或为每次运行生成文件名：

```python
# 拟定接口，尚未实现。
store = SessionStore.create(workspace_dir=workspace_dir, sessions_dir=sessions_dir)
store.session_id
store.path
store.write_record(record)
store.close()
```

`create()` 负责规范化存储目录、创建目录、生成唯一会话 ID、以 `x` 模式打开 `<session_id>.jsonl`，并写入和 flush 会话头；失败时关闭已取得的文件资源并传播错误。`close()` 关闭本实例持有的文件，应可重复调用。`write_record()` 每条写入后 flush；关闭后的写入明确失败。一个会话内顺序写入，不实现跨进程并发写同一文件。

当前用户已写的 `TraceWriter(stream)` 可保留为 SessionStore 内部的 JSONL 写入器。由 SessionStore 打开文件并传给它，SessionStore 是唯一的文件所有者；TraceWriter 不关闭借用的 stream。也可以将编码逻辑收为存储模块的私有函数，不需要同时维护两套对应用公开的写入接口。这里借用 Pi 的存储封装职责，选用持有流并逐条 flush 是本项目取舍，不是声称 Pi 也如此实现。

`Settings.sessions_dir` 默认 `~/.code_agent/sessions/`，与现有 `log_dir` 分开。不同工作目录的会话可以同放此目录，通过头部的规范化工作目录关联；同一工作目录多次创建会话得到不同 ID 和文件。会话头建议固定为 `version`、`type=session_start`、`session_id`、带时区的 `created_at`、`workspace_dir`。不整体序列化 Settings、环境变量或凭据。本步选择立即创建头部以尽早暴露路径/权限错误；不复制上游延迟创建机制。回放需先验证唯一会话头，再处理其下的运行分段；头部本身不代表已有一次完整运行。

### 保留现有核心入口，暂不新增 Session 包装类

当前 `Agent` 已持有本会话独立的 `ConversationHistory`，REPL 跨多次 `run()` 复用它，CLI 已在会话退出时调用 `Agent.close()`。因此建议继续使用 `create_agent → Agent.run → Agent.close`：

```text
CLI / REPL
  → core.create_agent：校验执行环境，创建并注入 SessionStore
      → Agent：持有 History 与本会话 store，编排运行与记录
          → SessionStore：ID、路径、文件及记录写入
              → TraceWriter：内部编码、write、flush
```

core 的组合入口用已校验的工作目录和配置创建存储；初始化后续步骤失败时，释放已取得的存储和模型客户端。Agent 接受存储依赖，不在运行循环中拼路径、调用 open 或生成会话文件名。CLI 只传配置、显示存储创建/运行错误和触发关闭，不接触 stream。当前不同时增加 SessionManager、SessionRepository、AgentSession 等相互转发的模块；以后状态归属确有变化，再考虑拆出会话编排。

`Agent.run()` 为每次运行生成本会话内唯一 run ID，记录开始与终态；沿统一历史提交路径，在 History 校验、提交和配对更新之后保存消息。完整 assistant 调用记录成功后才执行工具；user、tool、拒绝与取消补写均保存。UI 的 TextDelta/ToolCallEvent/ToolResultEvent 继续用于呈现，不承担持久化触发。存储不知道 Agent、模型、审批和终端；记录的业务时机由 core 决定。

一次 run 结束或正常取消不关闭会话文件，下一次输入继续使用它。`Agent.close()` 应尝试释放模型客户端与存储，即使其中一项关闭失败也要尝试另一项；运行主异常与记录/关闭错误应保留因果关系。沿用当前 M5a 的失败契约：写入失败停止后续模型/工具动作；已提交历史不回滚，取消补写时禁用失败记录接点，仍结清全部 pending。不能从缺少工具结果推断命令未执行。

### 后续实施顺序与验收

1. 先同步 `PLAN.md` 与 `docs/design/m5a-tracing.md`：用完整项目接入替换外部脚本管理文件的练习限定，补上会话头、默认存储路径和资源所有权。保留 M5b 恢复、M5c 记忆、M5d 检索的分阶段边界。
2. 准备 SessionStore 接口和关键测试，由用户实现存储：同工作目录两个会话不覆盖；头部关联正确；每条可立即读取；创建/写入/flush/关闭故障可见；测试目录显式使用 tmp_path，避免写真实用户目录。
3. 准备核心提交与收尾测试，由用户接入现有 Agent：同文件多次 run；工具调用写入失败时执行零次；已完成工具观察写入失败后不继续；取消补写失败仍结清 pending；退出时两类资源都释放。接通 `-p` 与 REPL 的默认记录。
4. 实现并验证带会话头的只读 replay：正常文件、未结束运行、残缺末行与中间损坏；不得调用模型和工具。补运行中读取、独立子进程强杀后读取有效前缀的证据，再按计划展示验收。

此建议修正此前“必须额外包装 core Session”及“项目入口必须提供 stream”的过强说法；具体类名和是否长期持有流都不是业界统一标准。当前只新增研究结果，尚未修改有效计划、应用实现或测试，也不代表 M5a 已验收。
