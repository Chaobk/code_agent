# 会话管理：创建与消息追加

当前实施 M5a 的会话创建与消息追加。2026-10-29 用户明确授权 Codex 调整 Store 和 Manager 的实现；后续默认分工仍是用户手写核心、Codex 提供接口、测试和评审。
本规格取代早期 M5a 草案中由外部脚本创建文件和传入 stream 的固定接口要求。

## 职责与数据

- `sessions/models.py`：`SessionMetadata` 保存 ID、标题、工作目录、创建和更新时间，不增加概述或重复的 History。
- `sessions/manager.py`：`SessionManager` 绑定工作目录，内部管理当前会话、History、Store 和保存失败状态；提供消息提交及历史快照读取。
- `context/history.py`：纯内存的消息校验、快照和工具调用配对，由 Manager 持有；不提供保存回调。
- `sessions/store.py`：`SessionStore` 是 Manager 内部的文件存储，不生成业务身份或调用模型；一会话一份 JSONL，元数据和消息共用该文件。
- `core/`：创建并连接上述依赖，负责 Agent 运行、记录时机，以及 M5b 的恢复编排。
- `ui/`：通过核心入口使用会话能力，不直接操作存储。

## 当前接口与行为

2026-10-29 用户确认默认集中保存到 `~/.codeagent/sessions/`，目录名不含下划线。
`Settings.sessions_dir` 提供默认路径，可通过 `CODE_AGENT_SESSIONS_DIR` 覆盖；
核心将工作目录和存储目录传给 Manager，Manager 内部创建 Store；工作目录通过会话元数据记录。
构造 Manager 时就创建并保存新会话，无须单独调用 `create()`。
用户决定当前暂不保存格式版本字段；版本兼容在实际需要时再设计。

`SessionManager(workspace_dir, sessions_dir)`：绑定并规范化工作目录，在内部创建 History 和 Store，
随后创建并保存一份新会话。初始化成功即有可追加消息的当前会话；Agent 只持有 Manager。
显式传入 `sessions_dir=None` 时使用纯内存会话，不创建 Store 或文件；CLI 始终传入配置路径。
元数据保留在 Manager 内部；不再提供单独的 `create()` 方法。

1. 创建发生在首次用户输入之前，此时不知道主题；title 为 None，不要求调用方提供标题。
2. 使用初始化时已绑定的工作目录。执行前的环境校验仍由核心完成，不增加创建工作目录或启动进程的副作用。
3. 生成新的 UUID4 会话 ID，使用带时区的当前时间；初始 created_at 与 updated_at 相同。
4. 构造元数据并调用一次内部 `store.create(metadata)`；保存成功后初始化结束。
5. 存储异常原样传播，不自动重试，初始化失败时不返回可用 Manager。异常本身不能保证存储完全没有副作用，也不影响其他已有 Manager 的会话。

`manager.append_message(message: dict[str, Any]) -> None`：复制消息，由内部 History 校验并
提交内存事实，随后使用当前会话 ID 调用 Store。校验失败不改内存或文件；保存失败时
保留已提交内存事实，记录第一次异常并原样传播，不自动重试。消息保持现有字典格式。
保存失败后的 Manager 禁止新对话和模型请求，只允许为已有 pending 调用追加合法工具
结果以结清内存配对；这些收尾记录不再写文件。`storage_error` 可读取首次保存异常。
文件可能已有部分写入，这不构成内存与文件的完整事务；损坏尾部读取规则待后续实现。

`get_messages()` 和 `get_pending_calls()` 返回内部 History 的独立快照，故障后仍可读取。
`get_model_messages()` 在会话保存健康且调用已配对时提供模型输入；读取均不触发保存。
Agent 的 `session_manager` 为必填，不再创建 History，也不保留 `agent.history` 别名。

核心执行流程按职责划分：`run()` 管理模型循环及结束判定，`_request_turn()` 负责
模型流组装，`_execute_calls()` 负责一批工具的审批、执行、结果事件及配对收尾。
当前调用和取消观察保留在工具批次内部；完整助手消息的提交也位于批次 try 内，保证
调用记录保存失败时工具执行零次，并在 finally 统一结清为 not_started。
工具结果保存失败时，其真实观察已在内存中，不再生成 unknown 覆盖；其余调用停止。
收尾若遇到首次保存失败，Manager 保留该观察并关闭写入，批次继续补齐剩余内存配对，
仅识别 Manager 记录的那个存储异常，不掩盖原始取消或工具故障。

`SessionMetadata.title` 为 str 或 None，None 表示尚未命名，不把“新会话”当成真实标题保存。
未来 UI 可显示 `title or "新会话"`，收到首条有效输入之后再考虑生成标题；本步没有接入
UI、自动标题或重命名，也不发起额外模型请求。

`SessionStore(sessions_dir)` 绑定根目录（expanduser、resolve），初始化不创建目录。
`SessionStore.create(metadata) -> None` 保存一个新会话：创建父目录，以 `x` 模式新建
`<session_id>.jsonl`，写入一条 UTF-8 JSON 对象和换行，flush 后关闭本次文件。
已有同 ID 的文件不得覆盖；打开、写入和 flush 失败原样传播，不自动重试。
Manager 生成的 session_id 为 UUID4 的 32 位小写 hex 字符串；Store 在创建目录前验证此格式，
并用这个稳定身份定位文件，避免将任意字符串解释为路径。
Manager 通过临时目录验证实际创建与消息保存，失败分支替换内部 Store 的相应操作。
同一工作目录可以创建多个独立的无标题会话；会话 ID 是稳定身份。

`SessionStore.append_message(session_id, message)` 校验 ID 后，以 `a` 模式追加
`{"type":"message","message":...}` 和换行，flush 后关闭；父目录已存在时允许创建缺失文件。
会话身份及当前会话的选择由 Manager 管理，文件操作失败原样传播。

## 单文件格式

首条记录为 `type=session_start`，其余字段为 session_id、title、
workspace_dir、created_at、updated_at。路径序列化为字符串，时间用带时区的 ISO 8601。

```jsonl
{"type":"session_start","session_id":"714e417c93264b2281ff18a154940081","title":null,"workspace_dir":"D:/project","created_at":"2026-10-27T08:00:00+00:00","updated_at":"2026-10-27T08:00:00+00:00"}
```

消息已在同一文件追加保存；运行分段和元数据更新记录在后续小步接入。重命名通过追加变更记录实现，
加载按顺序应用变更，不修改首条记录或重命名会话文件。后续记录结构在相应小步确定。
不为每个会话增加 metadata.json，不提前增加索引数据库。JSONL 编码和文件操作在 Store
内部处理，无须强制复用已有 TraceWriter。每次操作用 with 管理文件，当前不持有跨操作
文件流，也不需要一个只关闭空资源的 close 方法。默认根目录在核心配置接线时确定，
测试显式传入临时目录。

## 验证

`tests/code_agent/sessions/test_manager.py` 覆盖无标题元数据、目录规范化、同目录独立会话、
保存消息的顺序与工具调用结构、不同 Manager 的会话隔离，以及初始化/追加失败不重试和其他已有会话仍可使用。
测试不启动 Agent、不调用模型，文件副作用限定在临时目录。

`tests/code_agent/sessions/test_store.py` 覆盖会话头和父目录创建、同 ID 禁止覆盖、
不同 ID 文件独立、无效 ID 不创建存储，以及打开/写入/flush 失败传播和文件关闭。
测试使用临时目录。

运行：`.venv/Scripts/python.exe -B -m pytest tests/code_agent/sessions/test_manager.py -q -p no:cacheprovider`。
先运行 Manager 测试，再运行 Store 测试；后者使用真实文件但不访问模型。
消息保存由 Manager 协调；测试覆盖无效消息不落盘、保存失败保留内存事实、停止批次与续跑，
以及取消收尾不重复写入或覆盖取消、实际 JSONL 消息与历史一致。
2026-10-30 完整回归 413 项通过、2 项因 Git Bash 环境跳过，Ruff 检查通过。
只读读取、加载、重命名与运行边界记录分别在后续小步完善，当前不代表
完整 M5a 已完成或已获阶段验收。
