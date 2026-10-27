# 会话管理：创建会话

当前实施 M5a 的会话创建流程。本次用户明确授权 Codex 完成创建逻辑并提交；后续默认分工仍是用户手写核心、Codex 提供接口、测试和评审。
本规格取代早期 M5a 草案中由外部脚本创建文件和传入 stream 的固定接口要求。

## 职责与数据

- `sessions/models.py`：`SessionMetadata` 保存 ID、标题、工作目录、创建和更新时间，不增加概述或重复的 History。
- `sessions/manager.py`：`SessionManager` 决定创建、加载、重命名等会话业务；当前只实现创建。
- `sessions/store.py`：`SessionStore` 负责实际存储，不生成业务身份或调用模型；一会话一份 JSONL，元数据和消息共用该文件。
- `core/`：创建并连接上述依赖，负责 Agent 运行、记录时机，以及 M5b 的恢复编排。
- `ui/`：通过核心入口使用会话能力，不直接操作存储。

## 当前接口与行为

`SessionManager(store).create(workspace_dir) -> SessionMetadata`：

1. 创建发生在首次用户输入之前，此时不知道主题；title 为 None，不要求调用方提供标题。
2. 工作目录使用 expanduser 与 resolve 规范化。这里记录目录关联；执行前的环境校验仍由核心完成，不增加创建工作目录或启动进程的副作用。
3. 生成新的 UUID4 会话 ID，使用带时区的当前时间；初始 created_at 与 updated_at 相同。
4. 构造元数据并调用一次 `store.create(metadata)`；保存成功后返回元数据。
5. 存储异常原样传播，不自动重试、不返回成功对象。异常本身不能保证存储完全没有副作用。

`SessionMetadata.title` 为 str 或 None，None 表示尚未命名，不把“新会话”当成真实标题保存。
未来 UI 可显示 `title or "新会话"`，收到首条有效输入之后再考虑生成标题；本步没有接入
UI、自动标题或重命名，也不发起额外模型请求。

`SessionStore(sessions_dir)` 绑定根目录（expanduser、resolve），初始化不创建目录。
`SessionStore.create(metadata) -> None` 保存一个新会话：创建父目录，以 `x` 模式新建
`<session_id>.jsonl`，写入一条 UTF-8 JSON 对象和换行，flush 后关闭本次文件。
已有同 ID 的文件不得覆盖；打开、写入和 flush 失败原样传播，不自动重试。
Manager 生成的 session_id 为 UUID4 的 32 位小写 hex 字符串；Store 在创建目录前验证此格式，
并用这个稳定身份定位文件，避免将任意字符串解释为路径。
Manager 使用假存储验证业务行为，同时以真实 Store 验证创建闭环。
同一工作目录可以创建多个独立的无标题会话；会话 ID 是稳定身份。

## 单文件格式

首条记录为 `version=1`、`type=session_start`，其余字段为 session_id、title、
workspace_dir、created_at、updated_at。路径序列化为字符串，时间用带时区的 ISO 8601。

```jsonl
{"version":1,"type":"session_start","session_id":"714e417c93264b2281ff18a154940081","title":null,"workspace_dir":"D:/project","created_at":"2026-10-27T08:00:00+00:00","updated_at":"2026-10-27T08:00:00+00:00"}
```

以后同一文件追加消息、运行分段和元数据更新记录；重命名通过追加变更记录实现，
加载按顺序应用变更，不修改首条记录或重命名会话文件。后续记录结构在相应小步确定。
不为每个会话增加 metadata.json，不提前增加索引数据库。JSONL 编码和文件操作在 Store
内部处理，无须强制复用已有 TraceWriter。每次操作用 with 管理文件，当前不持有跨操作
文件流，也不需要一个只关闭空资源的 close 方法。默认根目录在核心配置接线时确定，
测试显式传入临时目录。

## 验证

`tests/code_agent/sessions/test_manager.py` 覆盖无标题元数据、目录规范化、同目录独立会话、
存储失败无重试并原样传播，以及 Manager 与真实 Store 保存 null 标题的闭环。
测试不启动 Agent、不调用模型，文件副作用限定在临时目录。

`tests/code_agent/sessions/test_store.py` 覆盖会话头和父目录创建、同 ID 禁止覆盖、
不同 ID 文件独立、无效 ID 不创建存储，以及打开/写入/flush 失败传播和文件关闭。
测试使用临时目录。

运行：`.venv/Scripts/python.exe -B -m pytest tests/code_agent/sessions/test_manager.py -q -p no:cacheprovider`。
先运行 Manager 测试，再运行 Store 测试；后者使用真实文件但不访问模型。
创建逻辑已实现。消息追加、加载、重命名和运行接线分别在后续小步实现，当前不代表
完整 M5a 已完成或已获阶段验收。
