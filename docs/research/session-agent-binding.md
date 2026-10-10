# 会话、Agent 配置与运行实例的关系

核查日期：2026-10-10。本笔记区分会话历史、Agent 角色配置与有状态运行实例，避免同名概念造成一对一关系的误解。仅依据官方文档与公开源码；源码分析未做真实服务运行验证。

## Codex：一条已加载对话有自己的运行状态

以下“会话”按用户可见的一条对话理解，对应 Codex 的 thread；官方协议中的 sessionId 还可能表示会话树根，不应直接等同于某个 Agent 对象。

1. 官方 App Server 文档把 Thread 定义为用户与 Codex 的对话，Thread 包含多次 Turn。支持 thread/resume 与卸载后的重新加载，说明持久对话与内存运行对象的生命周期不同。[官方文档](https://learn.chatgpt.com/docs/app-server)
2. ThreadManagerState.threads 按 ThreadId 保存 Arc<CodexThread>；创建时只向空缺键插入，重复启动会报错。恢复一个仍运行的 thread 时返回现有对象。由此可推论：在这个管理器内，一条已加载对话对应自己的运行对象。[thread_manager.rs](https://github.com/openai/codex/blob/main/codex-rs/core/src/thread_manager.rs#L399-L401)、[恢复与复用](https://github.com/openai/codex/blob/main/codex-rs/core/src/thread_manager.rs#L2052-L2087)、[注册](https://github.com/openai/codex/blob/main/codex-rs/core/src/thread_manager.rs#L2353-L2382)
3. CodexThread 持有 Arc<Session>；SessionState 中保存 history: ContextManager。因此对话历史属于会话范围的状态，而不是每次 Turn 的临时变量。[codex_thread.rs](https://github.com/openai/codex/blob/main/codex-rs/core/src/codex_thread.rs#L197-L209)、[state/session.rs](https://github.com/openai/codex/blob/main/codex-rs/core/src/state/session.rs#L62-L80)

核查来源为 2026-10-10 读取的 main 分支与官方文档，未锁定完整 commit；这些链接可能随开发变化。本文不据此断言私有桌面 UI 的完整内部实现。

## OpenCode：会话历史独立，角色可以切换

源码范围：官方 anomalyco/opencode 仓库 `dev` 分支，在上述日期逐文件核查。未取得固定 commit，以下链接及行号属于可变开发快照，不代表所有发布版本。

官方 Agents 文档明确支持在同一会话中通过 Tab 切换主 Agent，也说明子 Agent 可以创建子会话。因此 OpenCode 的 session 与 build、plan 等角色配置不是永久一对一关系。[官方 Agents 文档](https://opencode.ai/docs/agents/)

`Agent.Info` 包含名称、角色类型、模型、提示词、权限、采样参数等配置，不包含会话历史。这里的 Agent 更接近可重复使用的行为配置，而不是用户项目里持有 `messages` 的 Python 运行对象。[Agent.Info](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/agent/agent.ts#L32-L52)

历史记录按会话归属：`MessageTable.session_id` 关联 `SessionTable.id`，消息片段也保存 `session_id`。Session 表可以保存当前选用的 Agent 名称，但这个字段不是历史记录的归属主键。[会话和消息存储](https://github.com/anomalyco/opencode/blob/dev/packages/core/src/session/sql.ts#L48-L92)

创建用户消息时，`createUserMessage` 根据 `input.agent` 或默认配置找到 Agent，把名称记录到这条用户消息。若当前 session 保存的 Agent/模型不同，还会更新 session 的当前选择。运行循环以 `sessionID` 读取经过压缩过滤的历史，再用最近用户消息的 `agent` 查找本轮执行配置。助手消息同时记录 `sessionID` 和 Agent 名称。这些步骤支持保留同一会话历史并更换执行角色。[选择并记录 Agent](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/session/prompt.ts#L600-L650)、[读取历史](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/session/prompt.ts#L1027-L1041)、[执行角色选择](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/session/prompt.ts#L1108-L1137)

Task 工具按 `subagent_type` 找角色；首次调用通常创建带 `parentID` 的子 session，指定 `task_id` 时可恢复已有 session。随后调用 prompt 时传入子 session ID 与该角色名称。子任务因此有自己的历史归属，而不是把所有子 Agent 的原始对话混入父会话。[Task 工具创建/复用子会话](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/tool/task.ts#L120-L160)、[运行子会话](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/tool/task.ts#L182-L196)

## 对当前学习项目的推论

下列为设计建议，并非 OpenCode 的 Python 类设计：当前 `Agent` 同时负责执行循环并持有会话状态，所以让一个会话由一个 Agent 运行实例维护、在初始化时注入独立的 `History`，是合理的简化。不同会话不能共享同一个可变 History，连续多个 `run()` 可沿用同一份历史。

这不意味着同一份角色配置只能服务一个会话，也不意味着以后切换 Plan/Build 必须清空历史。未来需要多角色时，可以复用模型、工具与角色配置，同时保持会话历史和当前运行状态独立。当前不需要为此提前新增 Session 管理器或角色抽象。
