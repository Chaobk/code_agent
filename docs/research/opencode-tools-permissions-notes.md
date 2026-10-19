# OpenCode 工具与权限研究草稿

研究日期：2026-10-15。供合并到对比文档；本稿没有修改项目计划或业务实现。

## 版本范围

本文优先讲 **OpenCode V2**。官网首页的安装命令和文档入口已经指向 `/v2/`；但 GitHub `releases/latest` 本次仍重定向到 V1.19.0，其发布说明要求通过 `opencode upgrade --major` 升级 V2。因此不能从搜索结果中的 V1 页面或 GitHub 的 Latest 标记推断当前产品架构。[官网](https://opencode.ai/)、[V1.19.0 发布说明](https://github.com/anomalyco/opencode/releases/tag/v1.19.0)

源码核查使用当日可访问的 `anomalyco/opencode` **dev 分支**，`packages/core/package.json` 显示 `2.0.29`。这是分支内容的观察值，不声称它就是用户已安装或全部渠道已发布的版本；未固定到不可变 commit，链接后续可能变化。[包元数据](https://raw.githubusercontent.com/anomalyco/opencode/dev/packages/core/package.json)

## 模型能调用哪些工具

下表是 V2 官方工具目录的能力分类，不表示每次模型请求都拿到全部工具。[V2 Tools](https://opencode.ai/v2/docs/tools/)

| 工具或目录 | 用途 |
| --- | --- |
| `read` | 文件、目录、受支持的图片及 PDF 读取 |
| `glob`、`grep` | 文件名匹配、内容检索 |
| `edit`、`write`、`patch` | 文本替换、整文件写入、多文件补丁 |
| `shell` | 运行命令，可前台或后台执行 |
| `webfetch`、`websearch` | 获取网页、搜索网络 |
| `question`、`skill` | 询问用户、载入技能说明 |
| `subagent` | 创建或继续子 Agent 会话 |
| `execute` | JavaScript Code Mode，编排目录中的工具 |
| `opencode` 命名空间 | 会话重命名/迁移、worktree 创建/列举/删除 |
| `browser` 命名空间 | 桌面浏览器的页面交互与调试 |
| MCP、插件工具 | 来自所连接服务和所安装插件的扩展 |

工具名不等于权限名：例如 `edit`、`write`、`patch` 都按 `edit` 检查。支持的 GPT 模型可获得 `patch`，其他模型使用 `edit`/`write`；用户与 Agent 配置也会改变可用目录。[V2 Tools](https://opencode.ai/v2/docs/tools/)

**原生 function tools 与 Code Mode 并存，不能写成“只给模型一个 execute”。** 注册器把 `codemode:false` 的工具作为原生 schema 发送给 provider，其他工具进入 Code Mode catalog，然后添加 `execute` 入口；完全禁止的 action 会在快照阶段过滤。运行时仍验证工具是否属于当前请求。[注册器 `tool.ts`](https://raw.githubusercontent.com/anomalyco/opencode/dev/packages/core/src/tool.ts)

当日源码中的 `read`、`shell`、`subagent`、`question`、`skill` 以及编辑工具都显式设置了 `codemode:false`。MCP 默认走 Code Mode，可通过单个服务器的 `codemode:false` 改为原生工具。工具目录的层级名称因此不总等同于 provider 接收到的顶层 tool name。[read 源码](https://raw.githubusercontent.com/anomalyco/opencode/dev/packages/core/src/tool/plugin/read.ts)、[shell 源码](https://raw.githubusercontent.com/anomalyco/opencode/dev/packages/core/src/tool/plugin/shell.ts)、[subagent 源码](https://raw.githubusercontent.com/anomalyco/opencode/dev/packages/core/src/tool/plugin/subagent.ts)、[question 源码](https://raw.githubusercontent.com/anomalyco/opencode/dev/packages/core/src/tool/plugin/question.ts)、[skill 源码](https://raw.githubusercontent.com/anomalyco/opencode/dev/packages/core/src/tool/plugin/skill.ts)、[MCP 文档](https://opencode.ai/v2/docs/mcp-servers/)

## 权限判定与审批流程

V2 使用 `permissions` 有序规则，每条是 `action / resource / effect`。`allow` 直接执行，`ask` 等待审批，`deny` 拒绝。匹配采用整体通配符 `*`、`?`，最后命中的规则优先；没有匹配规则时是 `ask`。**这不等于出厂所有操作都询问**：基础规则先允许所有 action，再对外部目录与环境文件读取要求审批，`.env.example` 例外放行。[V2 Permissions](https://opencode.ai/v2/docs/permissions/)

`build` 因此默认具有广泛工具权限。`plan` 限制普通文件编辑；`explore` 只开放读取/检索等能力。父 Agent 能否启动子 Agent 由 `subagent` 权限决定，子 Agent 使用自身配置，不能想当然认为它只获得父 Agent 的工具权限子集。[V2 Agents](https://opencode.ai/v2/docs/agents/)

源码中的完整检查顺序可概括为：

1. 合并 Agent 规则与 session 规则，逐个 resource 求最后匹配值。
2. 任一 resource 的配置结果为 `deny`，立即返回拒绝。
3. 加上项目中已经保存的 approval；任一资源仍为 `ask`，创建待审批请求并等待，否则放行。
4. permission hook 可继续审核 `allow`/`ask`；配置的显式拒绝不会交给 hook 放宽。

这是一段确定性执行前检查，不能仅靠模型在提示词里判断“我有没有权限”。[权限服务 `permission.ts`](https://raw.githubusercontent.com/anomalyco/opencode/dev/packages/core/src/permission.ts)

三种用户回复：`once` 只处理当前请求；`always` 允许当前请求，并保存工具提出的模式；`reject` 拒绝当前及同一会话中其他待审批请求。保存模式未必是原始完整参数，例如 shell 可提出命令前缀，编辑工具可提出 `*`；不能把“始终允许”理解成仅允许当前文件或当前完整命令。[权限服务](https://raw.githubusercontent.com/anomalyco/opencode/dev/packages/core/src/permission.ts)、[编辑工具的 save 参数](https://raw.githubusercontent.com/anomalyco/opencode/dev/packages/core/src/tool/plugin/edit.ts)

**V2 的 always 是项目级持久授权**：写入数据库，按 `projectID` 读取，不是只留在内存中的 session grant，也不是自动编辑 `opencode.jsonc`。它不能覆盖配置中已生效的 `deny`。另有 session-scoped permissions，供 API/插件更新，和用户点击 always 是两套机制。[持久授权服务](https://raw.githubusercontent.com/anomalyco/opencode/dev/packages/core/src/permission/saved.ts)、[插件权限 API](https://opencode.ai/v2/docs/build/plugins#permissions)

## 危险 shell 命令怎样拦截

示例为本文编写的 V2 配置，表示命令默认询问、允许查看 Git 状态、禁止常见 push；它是演示，不是完整安全策略。

```jsonc
{
  "permissions": [
    { "action": "shell", "resource": "*", "effect": "ask" },
    { "action": "shell", "resource": "git status *", "effect": "allow" },
    { "action": "shell", "resource": "git push *", "effect": "deny" }
  ]
}
```

shell 工具先扫描命令，得到一个或多个 command resources 和建议保存的 prefix；先检查工作目录及推断到的外部目录，再统一调用 `permission.assert`，最后才启动进程。因此复合命令不只是拿整个字符串的开头匹配一次。[shell 执行实现](https://raw.githubusercontent.com/anomalyco/opencode/dev/packages/core/src/tool/plugin/shell.ts)

外部文件访问还需单独的 `external_directory` 检查，之后才是 `read`/`edit`。shell 的目录推断只是尽力分析；它拥有宿主用户的文件、进程和网络权限。默认 tree-sitter 与可选 portable scanner 不构成 OS 隔离层。[V2 Permissions](https://opencode.ai/v2/docs/permissions/)

**OpenCode 官方明确说它不为 Agent 提供沙箱。** 它把权限系统定位为让用户知晓和确认行为的交互机制。若需要真正隔离，官方建议在容器或虚拟机里运行；外部 MCP 服务行为也不在其信任边界内。[官方 SECURITY.md](https://raw.githubusercontent.com/anomalyco/opencode/dev/SECURITY.md)

据此可以推导：禁止 `edit` 不能自动禁止经 shell 写文件；禁止某个字符串形式的命令也不等于禁止所有可实现相同行为的脚本。应将“工具审批”“资源策略”和“OS 沙箱”分开理解，而不是把危险命令黑名单当成隔离保证。

## 管理策略、插件与 MCP

V2 另有 `policies`。`tool.use` 策略可在正常 permission 检查之后硬性阻止动作；`integration.use` 可阻止加载 MCP/skill/plugin。全局、系统、Console 管理的策略能够约束项目配置，policy 中的 allow 只是解除前面 policy 的 deny，之后仍需普通 permission 允许。策略不会隔离插件代码。[V2 Policies](https://opencode.ai/v2/docs/policies/)

MCP 的两层权限应分开：一层是连接服务所需的 OAuth/API key 及服务端 scope；另一层是 OpenCode 对本次工具调用的审批。适配器调用 MCP 前确实执行 `permission.assert`，action 为规范化的 `<server>_<tool>`，resource 与 always 保存模式都是 `*`。因此可以针对某个服务的工具统一 ask/deny，但这个默认适配器并没有按工具参数细分授权。[MCP 适配器源码](https://raw.githubusercontent.com/anomalyco/opencode/dev/packages/core/src/tool/mcp.ts)、[MCP 认证文档](https://opencode.ai/v2/docs/mcp-servers/#oauth)

插件可注册自定义工具，危险操作应在实现中主动调用 `ctx.permission.assert` 并声明 action/resources/save。不能认为任意第三方插件的所有副作用都会自动经过内置文件权限检查。[插件工具与权限 API](https://opencode.ai/v2/docs/build/plugins#permissions)

## V1 对照及尚不能统一的细节

| 概念 | V1 常见名称 | V2 名称 |
| --- | --- | --- |
| 权限配置 | `permission` 对象 | `permissions` 有序数组 |
| 命令工具/action | `bash` | `shell` |
| 子 Agent | `task` | `subagent` |
| 补丁工具 | `apply_patch` | `patch` |
| 扩展 | custom tools / MCP | plugin tools / MCP，支持 Code Mode |

部分 V1 配置可兼容迁移，但不宜把旧工具清单、旧字段和 V2 规则拼到一个示例中。[迁移指南](https://opencode.ai/v2/docs/migrate-v1/)、[V1 Tools](https://opencode.ai/docs/tools/)

发现一处明确的文档与 dev 漂移：V2 Tools 页称 `execute` 没有 `fetch`；当日 `codemode/tool.ts` 却明确提供 `fetch`，并挂载 `CodeModeWeb.extension`。该 extension 在源码里调用宿主 `globalThis.fetch`、拒绝 `file:`，未看到它复用 `webfetch` 的 permission assert。因此本稿不把“禁止 webfetch 就能切断全部网络”当成保证，也不据此声称已发布的所有 V2 安装都存在相同行为。[Code Mode 实现](https://raw.githubusercontent.com/anomalyco/opencode/dev/packages/core/src/codemode/tool.ts)、[web extension 实现](https://raw.githubusercontent.com/anomalyco/opencode/dev/packages/core/src/codemode/web.ts)
