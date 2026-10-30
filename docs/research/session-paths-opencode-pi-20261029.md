# 会话目录与工作目录：pi、OpenCode 的设计

核查日期：2026-10-29（Asia/Shanghai）。仅核对官方文档与源码，未运行上游产品。pi 使用官方 `earendil-works/pi` 的流动 `main` 引用，未锁定提交；OpenCode 的结构核查固定于 `v2.0.28`，默认位置另核对当前官方文档。不同页面的缓存可能来自不同时间，不将其视为同一构建快照。

## 默认存在哪里

| 项目 | 会话存储位置 | 如何关联工作项目 |
|---|---|---|
| pi | 默认 `~/.pi/agent/sessions/<encoded-cwd>/`，每会话一份 JSONL；可显式指定 `sessionDir` | 默认目录按规范化 cwd 编码分组；会话头另保存 `cwd` |
| OpenCode | 默认用户数据目录 `~/.local/share/opencode/` 下的 `opencode.db`；Windows 为 `%USERPROFILE%\.local\share\opencode` | `Session.Info` 保存 `projectID` 与 `location`，项目关联信息与数据库位置分开 |

pi 的默认路径函数、会话头、创建工厂均在 [SessionManager 官方源码](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/session-manager.ts)。OpenCode 默认位置及 `OPENCODE_DB` 覆盖说明见[官方 Storage 文档](https://opencode.ai/docs/troubleshooting/#storage)；会话结构见 [v2.0.28 Session.Info](https://github.com/anomalyco/opencode/blob/v2.0.28/packages/schema/src/session.ts)。这些默认值都不是把会话直接写到项目根目录。

## pi 的两个参数与绑定方式

pi 提供的静态工厂是：

```typescript
SessionManager.create(cwd, sessionDir?, options?)
```

`cwd` 表示工作目录，保存进会话头；`sessionDir` 表示文件保存目录，省略时由 cwd 计算默认目录，显式提供时直接使用该目录。内部构造函数分别保存 `this.cwd` 与 `this.sessionDir`。创建好实例以后，`newSession(options?)` 复用已绑定的 `this.cwd`，不要求再次传工作目录。[官方 SessionManager 源码：create、constructor、newSession](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/session-manager.ts)

因此应分清静态 `create()`（创建 manager 实例）与实例 `newSession()`（在已有 manager 上创建新会话）；它们与本项目实例方法 `manager.create(workspace_dir)` 并非仅凭名称即可逐一对应。

## 对 code_agent 的启发（设计推导）

工作目录与存储目录含义不同；但工作目录是在初始化时绑定，还是每次创建会话时传入，是另一个独立的接口选择。

如果本项目的 Manager 始终服务同一个工作目录，可以初始化时绑定一次：

```python
store = SessionStore(sessions_dir)
manager = SessionManager(store, workspace_dir=workspace_dir)
session = manager.create()
```

如果同一个 Manager 要为多个工作目录创建会话，保留 `manager.create(workspace_dir)` 也合理。Store 继续只负责文件位置；Manager 使用明确绑定或传入的工作目录生成元数据。

选择项目内存储时，可由组合入口令 `sessions_dir = workspace_dir / ".code_agent" / "sessions"`；选择用户目录集中存储时，令它指向用户目录。两者都能采用相同的 Manager 绑定方式。以上是可选设计推导，不修改当前实现，也不把上游类层次或数据库列为本项目要求。
