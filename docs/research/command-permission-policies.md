# 编程 Agent 的命令风险识别与权限判定研究

研究日期：2026-10-18。面向 `code_agent` 的命令权限学习：命令应该怎样分析，规则与模型各负责什么，以及怎样把结论落实到执行前检查。

本文是研究与设计建议，不是已经实现的能力，也不自动增加 [PLAN.md](../../PLAN.md) 的验收前置条件。核心实现继续由用户手写；本文没有修改源码、测试、依赖、权限设置或模型配置。

> 2026-10-18 计划调整：先做 M4a 多轮会话、M4b 失败与取消后继续，再在 M4c 一起学习命令判定与审批。M3 仅保留已实现的执行环境校验。下文规则与解析路线是 M4c 的候选设计，不是当前下一步；以 [PLAN.md](../../PLAN.md) 为准。

## 1. 先回答“关键词，还是模型？”

成熟 Agent 通常组合几种机制：解析输入、匹配权限规则、评估审批需求、必要时由人或独立模型审查，最后由执行环境限制实际副作用。不同产品、模式和版本的组合不同，不能概括成一份关键词黑名单，也不能概括成“模型自己决定能否执行”。[C-rules]、[C-auto]、[A-permissions]、[A-modes]

需要纠正此前讨论中的一个过度简化：**主任务模型不应仅凭自己的解释绕过授权，但独立审查模型确实可以在产品规定的范围内批准或拒绝动作。**Codex Auto-review 和 Claude Code auto mode 都有这种机制；这不要求本项目同步实现模型审批。[C-auto]、[A-modes]、[A-engineering]

| 层次 | 主要问题 | 典型实现 | 无法单独保证的事情 |
|---|---|---|---|
| 参数校验 | 工具参数是否合法？ | JSON/schema、类型和取值范围 | 合法字符串仍可能有危险副作用 |
| 命令分析 | 哪些片段会被执行，哪些只是参数？ | shell AST、受限语法扫描、argv 提取 | 动态变量、脚本、程序内部行为可能无法静态确定 |
| 规则匹配 | 此调用是否命中预先声明的权限？ | argv 前缀、通配模式、参数条件、内置启发式 | 未命中拒绝规则不等于安全 |
| 审批与风险评估 | 此动作是否符合本次用户授权和影响范围？ | 人工审批、独立 reviewer/classifier | 判断可能出错，不能替代执行边界 |
| 执行隔离 | 进程实际上能访问什么？ | 文件系统、网络和 OS 沙箱 | 仍需正确配置；被授权目录内也可能发生破坏 |

上表是综合分析框架，不表示各产品都按同一固定顺序实现。[C-approvals]、[C-sandbox]、[A-sandbox]

## 2. 研究范围与证据强度

| 对象 | 本次核实范围 | 版本边界 |
|---|---|---|
| Codex | 官方规则/审批文档、公开 Rust 源码、本机只读规则检查实验 | 源码固定为 `8c076942cb70fba8e5c4732c23f1faabc4aed484`；安装 CLI 为 `0.160.0`，两者分别记录，不假定它们完全同版 |
| OpenCode | 官方权限文档与固定 dev 源码；第 4 节区分文档叫法与当前实现 | 固定源码 `c38d83c0c4f92466ff9c8e12b8a741698e811012`，不沿用旧研究中的目录和 TODO |
| Claude Code | 官方权限、auto mode、hooks、sandbox 文档与 Anthropic 工程说明 | 文档读取日期为本次研究日期；不声称验证了闭源 CLI 内部源码 |
| Gemini CLI | 官方 Policy engine 文档作补充对照 | 只确认文档明确说明的规则层，不推断整个产品完全不使用模型 |

Codex 源码通过 GitHub 官方 API 按固定提交直接读取；本次未使用旧研究里的第三方代理或镜像作为证据。搜索结果只用于定位，实际结论来自已打开的正文与源码。代码链接固定到提交，文档链接可能随发布更新。

## 3. Codex：argv 规则、两条分析路径与独立 reviewer

### 3.1 权限规则匹配的是参数前缀

官方 `prefix_rule` 的 `pattern` 是有顺序的参数列表，结果为 `allow / prompt / forbidden`；同时命中多条规则时取最严格结果。规则还可以带解释和加载时校验的正反样例。它不是在整段字符串中搜索危险词。[C-rules]、[C-engine]、[C-decision]

例如，模式 `["git", "reset", "--hard"]` 可以匹配直接形式，但不能据此声称覆盖了 `git -C repo reset --hard`、所有参数重排或其他程序转发形式。后者需要额外解析、规则或审批。**用户配置规则与产品内置风险启发式也应分开讨论。**

固定源码的规则层还支持可执行文件身份信息：精确路径与 basename 回退有不同匹配语义，`host_executable` 可以限定路径。本项目若以后实现自动放行，不能不加区分地把任意 `/somewhere/git` 都视为受信任的系统 Git。[C-engine]

### 3.2 能完整分析时才拆成多条命令

`bash.rs` 使用 tree-sitter-bash。用于规则拆分的入口只接受受支持的字面命令序列，对命名节点和操作符采用允许集合；发现解析错误、动态展开、重定向或不支持结构时不返回完整 argv 列表。[C-parse]

对于受支持的 `git status && git reset --hard`，可分别评估两个调用，汇总时采用更严格的结论。若无法安全拆分，核心回到整个 shell invocation，再走规则、启发式和沙箱/审批政策。**不能把“不能拆分”一概写成自动允许，也不能一概写成 Codex 必然拒绝。**[C-rules]、[C-route]

### 3.3 “找出危险片段”不等于“证明整个脚本安全”

同一解析模块还有一条更宽松的字面命令提取路径。它可以从复杂语法中寻找已知的命令片段，但源码明确要求不能用它来证明安全。[C-parse]

固定源码的危险启发式包含强制 `rm` 的识别：检查 `--force`、组合短选项中的 `f`，并尊重 `--` 终止符；还处理有限的 `sudo`、`env`、`trap` 包装和嵌套深度。其测试同时展示：字面危险片段可以命中，但动态构造或错误语法可能不命中。这里举的是已读取文件中的规则，不是“Codex 能识别全部危险命令”的结论。[C-danger]

内置启发式标记危险后，是否转为确认还是拒绝，仍由审批政策等条件决定；例如不允许询问时，某些本应确认的调用会被拒绝。对于未命中规则的动作，核心还会考虑沙箱和是否请求越界权限。[C-route]

### 3.4 模型审批是独立的一层

启用 Auto-review 时，原本需要审批的动作可交给单独的 reviewer agent；常规沙箱内允许的动作并非一律再调用审查模型。reviewer 会读取具体动作和有限上下文，必要时进行只读核实。源码中有风险等级、用户授权、allow/deny 与理由的结构化结果。[C-auto]、[C-assessment]

独立审查可以区分“用户已授权向自己的仓库推送”和“向不明仓库发送私有代码”等仅靠命令名难以判断的情况。它改变的是审批处理方式，不是解除沙箱或覆盖组织约束。审查错误、超时等情况不能被当成默认放行。[C-approvals]、[C-auto]

### 3.5 本机检查结果不等于完整执行实验

本次只使用 `codex execpolicy check` 评估临时规则；命令参数只是数据，没有执行 `git reset`、`git clean` 或任何模型请求。安装版本：`codex-cli 0.160.0`。

临时规则：`git` 前缀 allow、`git reset --hard` 前缀 forbidden、`git clean` 前缀 prompt。宽 `git` allow 只是用于验证冲突优先级，不是推荐配置。

| 送入检查器的 argv | 实际结果 | 命中数 |
|---|---|---:|
| `git status` | allow | 1 |
| `git reset --hard` | forbidden | 2 |
| `git clean -fd` | prompt | 2 |
| `python --version` | 无 decision 字段 | 0 |
| `echo` 加一个文字参数 | 无 decision 字段 | 0 |
| `bash -lc "git status && git reset --hard"` | 无 decision 字段 | 0 |

最后一行尤其重要：**独立规则检查器评估传入的 argv；完整 harness 在另一层进行 shell 拆分。**不能拿这条结果证明 Codex 的执行流程不会检查复合命令。无匹配时省略 decision 也是规则检查器的行为，不能直接解释成最终执行允许。[C-engine]、[C-route]

## 4. OpenCode：语法分析、资源规则与执行前授权

### 版本与证据范围

本节源码固定为官方仓库 `anomalyco/opencode` 的 dev 提交 `c38d83c0c4f92466ff9c8e12b8a741698e811012`，通过 GitHub 官方 API 在研究当日取得。它是研究用的开发分支快照，不能据此断言所有已发布客户端采用相同行为。固定提交：[commit](https://github.com/anomalyco/opencode/commit/c38d83c0c4f92466ff9c8e12b8a741698e811012)。

官方在线权限文档使用 `bash` 作为工具名，而本快照代码使用 `shell`。网上可搜到旧 `packages/opencode/src/tool/bash.ts`，或较早 V2 快照中“待移植解析器”的 TODO；这些不能替代本次当前路径。以下实现结论均来自本次实际读取的固定提交源码；测试结论是“源码中已有对应断言”，本次未安装或运行 OpenCode 全套测试。[官方权限文档](https://opencode.ai/docs/permissions/)、[当前工具定义](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/src/tool/plugin/shell.ts#L23)

### 执行链：先识别，再授权，最后启动

工具的 `prepare()` 先解析工作目录，再扫描命令，收集目录切换目标，先请求 `external_directory` 权限，然后请求 `shell` 权限。审批后还会再次检查工作目录确为目录，避免等待期间目录已被删除或替换。其外层把 `prepare()` 作为 `Shell.create()` 的执行前回调。[工具入口 L117-L154、L203-L219](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/src/tool/plugin/shell.ts#L117)

流程可概括为：

```text
模型提出 shell 工具参数
  → 解析 workdir
  → 扫描 command，产生命令资源和目录目标
  → 外部目录授权
  → 每个命令资源的 shell 授权
  → 再检查 cwd
  → 使用实际 command/cwd/shell/env 启动进程
```

进程服务先执行插件 `shell.create.before` hook，再调用工具传入的执行前回调，之后才构造并启动子进程。因此上面的校验针对该时点的 invocation；不能把展示给模型的原始参数直接视为唯一执行输入。[进程服务 L262-L315](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/src/shell.ts#L262)

### 两条解析路径：tree-sitter 与 portable scanner

当前不是只有一套关键词匹配。代码保留两条路径：[parse.ts L172-L236](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/src/shell/parse.ts#L172)

| 路径 | 如何识别 | 如何选中 |
|---|---|---|
| legacy | 使用 `web-tree-sitter` 与 Bash/PowerShell grammar，遍历 AST 的 `command` 节点 | portable 未启用且 Bash WASM 资产存在 |
| portable | 项目自写扫描器，输出 `scanned` 命令列表或 `opaque` 原因 | 显式配置、local/dev channel 默认开启，或 WASM 路径为空 |

工具中的选择受 `experimental.portable_shell_scanner` 配置控制；local/dev 默认开启可以被配置覆盖。WASM 路径为空时 `ShellParse.scan()` 自身仍会选 portable，因此显式 false 也不会让无资产环境加载不存在的 Bash WASM。[选择条件 L126-L129](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/src/tool/plugin/shell.ts#L126)、[扫描入口 L172-L182](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/src/shell/parse.ts#L172)、[Node WASM 配置](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/src/shell/parser-wasm.node.ts)、[workerd 空资产配置](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/src/shell/parser-wasm.workerd.ts)

扫描器负责辨认语法和提取权限资源，不直接决定命令“安全”。`scan.ts` 明确区分 `opaque`（解析能力限制）与权限决策，并定义动态命令名、动态执行、无效结构、未闭合引号等原因。输入长度和递归工作预算也有界。[扫描结果契约 L3-L31](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/src/shell/scan.ts#L3)、[扫描入口 L216-L228](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/src/shell/scan.ts#L216)

### command、args、复合命令与可复用审批

legacy 的 `parts()` 提取命令名与参数文本；`command` AST 遍历会发现复合语句中的多个命令。用于匹配规则的 `resource` 保留源码命令文本；若该命令的父节点是 `redirected_statement`，使用包含重定向的父节点文本。不是把所有参数拼成一个“是否危险”的关键词结论。[AST 遍历 L184-L217](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/src/shell/parse.ts#L184)、[参数提取 L301-L316](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/src/shell/parse.ts#L301)

例如：

```text
输入：git status && npm run test -- --watch

规则匹配资源：
  git status
  npm run test -- --watch

工具建议保存的审批模式：
  git status *
  npm run test *
```

这是官方解析测试中的具体预期；工具层也有 `printf one && printf two` 被拆成两个 resources、一次批量请求的断言。[解析测试 L8-L20](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/test/shell-parse.test.ts#L8)、[工具测试 L1060-L1082](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/test/tool-shell.test.ts#L1060)

`save` 前缀来自 ARITY 表：例如 git 保留两个词、npm run 保留三个词；未知命令通常只保留第一个词。它用于用户选择 always 时建议复用的授权范围，不能把“前缀可复用”解释成“前缀下所有行为都安全”。[ARITY 与 prefix](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/src/shell/parse.ts#L23)、[prefix 实现 L375-L381](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/src/shell/parse.ts#L375)

portable 并非遇到所有复合语法或命令替换都拒绝。官方测试要求其在管道、`$(...)`、条件语句、声明中的命令替换、重定向目标中的命令替换、算术展开、heredoc 等受支持场景中与 legacy 资源保持一致。另有工具测试断言嵌套命令被 deny 时替换不执行。[语法与替换测试 L51-L78](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/test/shell-parse.test.ts#L51)、[嵌套 deny 测试 L532-L560](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/test/tool-shell.test.ts#L532)

### allow / ask / deny 与规则优先级

规则匹配 action 与 resource，使用 wildcard；同一资源最后一条匹配规则生效，没有匹配时求值器返回 ask。因此规则顺序有语义，“更具体”并不自动优先。官方文档说明 `*` 和 `?`，并建议通配默认在前、具体规则在后。[求值器 L87-L101](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/src/permission.ts#L87)、[wildcard 源码](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/src/util/wildcard.ts)、[官方文档规则顺序](https://opencode.ai/docs/permissions/#granular-rules-object-syntax)

对一次请求的多个资源，当前实现先按 agent/session 的配置规则检查：只要任意资源最终效果是 deny 就立即拒绝，已保存的 allow 不能覆盖这一拒绝。之后才加入项目已保存授权；仍有资源 ask 就请求确认，其余可 allow。[配置与批量求值 L148-L188](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/src/permission.ts#L148)

这里有两个层次，不能混为“deny 永远覆盖一切”：

- 同一资源在配置规则集内部仍是最后匹配胜出，可以由后面的配置规则改写前面规则。
- 配置求值后的 deny 会先于 saved approvals 检查，不能被用户以前保存的 allow 穿透。

### 人工确认、保存授权与模型的角色

`permission.assert()` 根据最终效果分别拒绝、返回或挂起等待；ask 发布事件后等待 reply，reply 枚举为 once / always / reject。always 将工具给出的 `save` 资源模式存入按 projectID 关联的数据库表，而不是只改本次函数的返回值。[assert L231-L263](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/src/permission.ts#L231)、[reply L265-L321](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/src/permission.ts#L265)、[数据库保存 L37-L77](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/src/permission/saved.ts#L37)

官方在线文档把 always 描述为当前会话内生效；本快照的实现则显式读写项目级 PermissionSaved 数据库。本文按来源分别记录，不把在线文档的存续期表述当成该提交代码的结论。[官方确认文档](https://opencode.ai/docs/permissions/#what-ask-does)、[本快照 savedRules](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/src/permission.ts#L148)

在已追踪的默认路径中，语法提取和规则求值均为确定性代码，没有在这两步调用 LLM 判定危险程度。模型负责提出工具请求，最终能否执行由宿主权限服务约束；“模型觉得安全”本身不是 allow。[扫描入口](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/src/shell/parse.ts#L172)、[规则求值](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/src/permission.ts#L173)

但权限服务有 `permission.evaluate` 插件 hook：配置 deny 会先返回，非 deny 的计算结果可交 hook 调整。此处只能证明核心默认实现不依赖 LLM，不能推断所有第三方插件或所有部署都绝不使用模型参与审批。本次未穷尽这些扩展。[插件评价 hook L173-L188](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/src/permission.ts#L173)

### 解析失败：两条路径的行为不同

portable 的 `opaque` 会在 `scanPortable()` 转成失败。工具 prepare 失败发生在授权和进程启动之前；该路径没有“解析不了就按普通字符串放行”、静默回退 legacy 或再请模型批准的分支。[portable 失败 L220-L236](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/src/shell/parse.ts#L220)、[执行前回调](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/src/tool/plugin/shell.ts#L117)

legacy 只明确检查没有返回 tree 的情况，未在此处检查 tree-sitter 的语法错误标志。不能据其使用 AST 就断言其遇到无效语法也一律拒绝。[legacy L184-L217](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/src/shell/parse.ts#L184)

官方工具测试给出清晰反例：输入先写 marker、随后出现未闭合引号：

```sh
printf hello > marker
echo "
```

该测试期待 portable 返回 unterminated-quote、没有权限请求、没有 marker；legacy 则进入 shell 授权，执行退出码非零，但前面的 marker 已写成 hello。这是测试源码声明的路径差异，本次未在本机运行复现。[明确区分两条路径的测试 L1251-L1288](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/test/tool-shell.test.ts#L1251)

### 外部目录检测的实际范围

本快照的 shell 路径只向 FileAccess 送入 workdir 和解析器提取的目录切换目标。解析器特别处理 cd/chdir/popd/pushd/Push-Location/Set-Location，能展开部分确定的目录值；包含未知 shell 表达式的目录不会被当作可静态解析目标。[目录命令集合](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/src/shell/parse.ts#L14)、[目录参数与展开 L318-L367](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/src/shell/parse.ts#L318)、[授权调用 L123-L136](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/src/tool/plugin/shell.ts#L123)

FileAccess 用词法路径规范化与 contains 判断是否在当前 location 或项目目录中，外部目录形成 `external_directory` 资源并去重授权。这段代码不是按真实文件系统访问逐次拦截，也未在此处通过 realpath 消除所有符号链接影响。[词法路径与边界 L66-L123](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/src/file-access.ts#L66)、[批量目录授权 L125-L141](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/src/file-access.ts#L125)

因此，不能从该检查推出 `cat /outside/file`、`python script.py`、重定向到外部文件，或运行中动态生成路径都被完整覆盖。这个判断来自已追踪的调用范围；不等于本次已对每种绕过方式完成实测。解析器保留重定向文本，也不等于已建立读写目标列表或操作系统强制文件隔离。

本地环境驱动直接使用宿主 node:fs，并接受传入的进程 spawner；shell 服务通过 Environment spawner 启动。权限层和执行环境是不同层。是否有额外容器、远端 driver 或 OS 沙箱，需要分别查对应环境实现，不能从 external_directory 的存在推定有隔离，也不能据本节断言所有 OpenCode 环境都没有隔离。[local driver](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/src/environment/local.ts#L9)、[shell spawn L300-L318](https://github.com/anomalyco/opencode/blob/c38d83c0c4f92466ff9c8e12b8a741698e811012/packages/core/src/shell.ts#L300)

### 对 code_agent M4c 的学习启示

以下是基于上述实现的设计建议，不是 OpenCode 的强制规格，也不自动扩大本项目里程碑：

1. 将“识别命令结构”和“决定 allow/deny”分成概念上的两步。关键词规则可用于少量明确场景，但应说明它匹配什么输入、哪些 shell 语法不在覆盖范围。
2. 保留原始命令用于执行和审计；若引入拆分，明确规则匹配的是整个输入、每个命令节点还是 argv，尤其测试引号、复合命令、重定向、命令替换。
3. 明确规则优先级和批量决策。不要凭“黑名单通常优先”的印象推导真实规则结果。
4. 明确解析失败政策。若未来采用受限语法解析器，不能把解析异常静默当 allow；与此同时，单有 AST 并不自动实现此政策。
5. 拒绝必须发生在启动进程之前。环境预检与执行前再次验证是两个时点；确认等待会让文件系统状态变化。
6. 本项目 M3 已实现执行环境校验与固定 cwd；危险命令规则与人工 ask/终端确认一起后移到 M4c。未来规则只能按实际覆盖范围描述，不能宣称完整工作区文件隔离。

### 来源审计与未覆盖项

- 已直读：官方权限文档；GitHub 官方 API 返回的固定提交与完整 tree；该提交的 shell 工具、shell 服务、parse/scanner、Permission、PermissionSaved、FileAccess、local environment 和对应解析/工具测试。未使用第三方代理镜像或把 issue 讨论当实现证据。
- 网页搜索缓存可指向较早目录和 TODO；本节以固定提交的官方原始文件为准。源码由官方 GitHub contents API 取回并按 base64 解码，不以搜索摘要代替代码。
- 未运行 OpenCode 全套测试，未做跨发行版行为矩阵，未证明所有插件实现和所有 driver 的安全性质。
- 官方另有实验性 `policies` 文档，明确它控制 provider 等命名资源，与会话工具权限是不同机制；本文的“shell 权限”不要和该配置混称。[官方 Policies](https://opencode.ai/docs/policies/)


## 5. Claude Code 与 Gemini CLI 的补充对照

### 5.1 Claude Code：确定性规则先行，auto mode 可使用分类模型

Claude Code 的权限规则由程序执行，优先级为 deny → ask → allow。官方说明其识别复合命令，并分别检查有关片段；同时明确 Bash 文本规则有覆盖边界，例如某个命令名模式不自动等价于所有绝对路径、shell 包装和执行方式。[A-permissions]

auto mode 会让独立 classifier 在规定的范围内审查动作。显式规则、宽 allow 的特殊处理和模式设置影响哪些动作进入分类器；不能简化为“每条命令都问模型”，也不能简化为“产品从不通过模型批准执行”。Anthropic 的工程说明描述了减少不可信上下文影响和分阶段分类的设计，并承认仍会有误判。[A-modes]、[A-auto-config]、[A-engineering]

PreToolUse hook 是另一种可编程闸门，可返回权限决定并修改工具输入；修改后的输入需要重新参与权限判断。hook 的 allow 不能据此绕过匹配的 deny/ask。OS sandbox 则限制 shell 及其子进程可访问的文件和网络，两者不能互相替代。[A-hooks]、[A-sandbox]

### 5.2 Gemini CLI：不能照搬其他产品的规则优先级

官方 Policy engine 支持 TOML 规则，以工具名、参数匹配和命令前缀等条件产生 `allow / deny / ask_user`；按优先级选择匹配规则。它与 Codex 的“取最严格匹配”、OpenCode 文档的“最后匹配优先”并非同一套配置语义。本轮只用它对照确定性规则引擎，不对未核实的其他风险审查机制下结论。[G-policy]

## 6. 几类容易混淆的识别方式

| 方式 | 能做什么 | 典型问题 | 本项目建议 |
|---|---|---|---|
| 整段字符串 contains / 简单正则 | 很快挡住少量文本模式 | 把引用文字当命令；空格、选项位置、包装形式影响命中 | 不作为唯一判据 |
| shell-like 分词 | 正确处理部分引号和空格，得到 token | 词法信息不等于完整 shell 语义 | 仅用于明确的受限子集 |
| AST / 受限 shell 扫描器 | 区分调用、引号、操作符和部分嵌套结构 | 动态展开、外部脚本和程序内部副作用仍有边界 | 需要复合脚本支持时研究；不要只收集 command 节点就宣称安全 |
| argv / 参数规则 | 确定性匹配命令、子命令、选项或用户权限 | 需定义路径、包装和选项规范化；规则有优先级 | M4c 的主要学习对象 |
| 独立模型审查 | 根据意图、目标、数据与授权评估上下文风险 | 成本、延迟、误判、上下文污染和失败处理 | 可后续实验，当前不是验收前置条件 |
| OS 强制隔离 | 对实际文件/网络访问设边界 | 配置与平台差异；边界内仍可能破坏数据 | 与字符串策略分开验收 |

此表是基于前文的设计比较，不是各产品默认配置清单。

### 6.1 为什么单用 shlex.split 还不够

Python 文档把 `shlex` 定义为简单词法分析工具，shell 兼容性设置也不执行语义有效性验证；其 quoting 目标是 Unix shell，不应直接拿来分析 PowerShell/cmd。[P-shlex]

本次在项目 Python 中仅执行分词，得到：

~~~text
输入：echo '$(git reset --hard)'
结果：["echo", "$(git reset --hard)"]

输入：echo "$(git reset --hard)"
结果：["echo", "$(git reset --hard)"]

输入：git status&&git reset --hard
结果：["git", "status&&git", "reset", "--hard"]
~~~

前两条分词结果相同，却不能仅凭 token 判定原脚本的展开语义；第三条也没有自动拆成两条命令。因此，`tokens[0] == "echo"` 或只看第一段 argv 不能作为完整脚本的放行依据。**本次没有执行这些待分析脚本。**

### 6.2 “命令风险”还取决于对象和上下文

规则匹配需区分动作、目标、选项与本次授权。比如 `git reset --hard` 与仅恢复指定文件不是同一影响范围；清理命令的预览选项也会影响是否真正删除。具体命令语义应查其手册，不能凭名字猜。[Git-reset]、[Git-clean]

同样，`pytest` 会运行项目代码，`python` 可以执行任意脚本。即使命令名没有出现在拒绝表中，也不能证明它没有副作用；这是规则与隔离要分层讨论的原因。

## 7. 对 code_agent 的实现建议：先选范围，再写规则

以下是后续规则完善的研究建议。基础 allow / ask / deny、确认与结果回灌已经接线；命令解析和授权范围细化按 [PLAN.md](../../PLAN.md) 后置，不作为当前 M5a 的前置条件。

### 7.1 对外接口可以保持很小

此前的 `PolicyDecision(allowed, reason)` 与 `evaluate_bash_command(command)` 未成为现行接口。当前实现使用 `Decision`、`ApprovalPolicy` 与 `ApprovalGate`；后续命令分析仍应独立于确认输入、工具执行和历史写入，至少区分：

- 命中了本版明确拒绝规则；
- 支持并分析了该输入，但没有命中拒绝规则；
- 语法错误、解析失败或不支持分析。

后两者不能混成“没有看见危险词，所以安全”。允许返回值应解释为在已声明策略范围内获准执行，而非数学意义上的安全证明。

### 7.2 两条可选路线，不提前建设完整 Bash 解释器

| 路线 | 实施内容 | 取舍 |
|---|---|---|
| 受限简单命令 | 明确仅支持字面单命令与静态参数；词法分析保留必要的原始语法信息；对动态/复合/不支持形式显式处理 | 适合先学参数规则，但会限制现有 Bash 使用方式；不能只靠 split 后的 token 声称输入属于该子集 |
| Bash 语法分析 | 采用明确版本的解析器，识别命令序列、引用、重定向和嵌套；只对支持的节点给出完整分析结果 | 更贴近成熟实现，但增加依赖和边界测试；tree-sitter 不是自动安全保证 |

若在 M4c 选择第一条，-p 单次模式可以对“需要进一步确认但当前不支持”的输入直接拒绝，并明确理由；是否缩小现有脚本支持范围必须先由用户决定。若保留复杂脚本的原有执行能力，只能把少量规则称为启发式保护，并如实记录漏检边界。**本文不默默把“必须支持完整 AST”或“必须拒绝全部复杂脚本”变成验收前置条件。**

### 7.3 拒绝规则按命令语义写

建议先选极少的命令族：

- `git reset` 的破坏性模式：识别子命令与 `--hard` 选项，而不是搜索正文中的 hard。
- `git clean` 的删除模式：定义组合短选项、长选项、预览模式与参数终止符的处理；`-fd` 与 `-df` 不能因顺序不同漏掉。
- 明确是否覆盖 `git -C ...`、绝对可执行路径、`env` 和嵌套 shell；不支持时给出可观察结果，不假称已覆盖。

用于拒绝风险的 basename 启发式与用于自动授权的可执行身份识别，要求可以不同。前者多挡一点可能只是误拒绝；后者把任意同名程序当成受信任程序，可能放大权限。

只针对约定的字面命令先实现和测清楚，复杂包装按实际失败案例扩展。本项目不需要因为成熟产品有 reviewer，就自动引入第二次模型调用。

### 7.4 规则冲突和默认行为必须显式定义

例如可约定拒绝优先于普通允许，并保留 reason；但这是本项目的选择，不能以“所有 Agent 都这样做”为理由。还应写清无匹配、不支持语法、解析器错误、可选 reviewer 超时各走什么路径。不要让异常静默变成允许。

若以后采用自动审批，至少分开主任务模型和审查职责，传入确切动作及相关用户授权，记录可核查的短理由；校验返回结构，并为拒绝、异常、超时和取消设计不执行的路径。[C-approvals]、[A-engineering]

### 7.5 接入位置：校验之后、产生副作用之前

本项目当前只向模型提供 Bash。策略应分析工具参数中的原始 `command`，而不是简单照搬某个产品仅识别三元素 `bash -c script` wrapper 的函数；本项目实际启动参数还有 `--noprofile --norc`。

当前已贯通的连接顺序：

~~~text
Registry 校验工具参数
  → 调用已注入的 Bash 策略
  → 允许：执行实际 handler
  → 拒绝：生成未执行的 ToolResult
  → 需确认：交互入口询问；单次批准后执行，否则生成未执行观察
  → core 按 call_id 写入历史并发送结果事件
~~~

core 负责连接策略与工具；Registry 参数校验后的 `before_execute` 交给核心协调策略与 Gate。后续完善应复用该接点，避免在 Bash handler 内再加一条不一致的审批路径。

普通拒绝可复用现有 ToolResult 错误语义，在 output 中说明理由和 `execution="not_started"`；它是模型可据以修正的观察，不必立刻终止整个运行。取消则继续沿用已有取消契约。实际决定字段名与接线接口时再补测试，不把本文伪代码当作现有接口。

## 8. 建议的验收用例

以下是待选择的测试矩阵，**不是已经运行通过的新增测试**。危险命令只作为字符串送给分析器；集成测试用假 handler 断言执行次数，不运行真实破坏命令。

| 输入/场景 | 需要验证的事情 |
|---|---|
| `git status`、`pytest -q` | 普通允许路径不被误拒绝；允许不等于只读 |
| `git reset --hard` | 命中指定拒绝规则，理由明确 |
| `git clean -fd`、`git clean -df`、分离短选项 | 约定的等价选项形式一致 |
| `echo "git reset --hard"` | 引用文字不误当外层命令 |
| `echo "$(git reset --hard)"` | 嵌套调用被分析或明确标为不支持，不能仅因外层 echo 而放行 |
| `git status && git reset --hard`，以及无空格操作符 | 检查所有受支持片段，不能只看第一条 |
| `printf x > important.txt` | 重定向本身可能写文件；不能只依据命令名判断只读 |
| 单引号/双引号、转义、注释、跨行输入 | 分析方式与声明支持的语法一致 |
| `git -C repo reset --hard`、绝对路径、包装命令 | 支持范围明确，不支持时给理由 |
| 动态变量、here-doc、循环、解释器脚本 | 不能用静态未命中宣称没有副作用 |
| 语法错误、解析器异常 | 不执行，或按明确采用的有限保护策略处理 |
| 模型修正一次被拒绝调用 | 拒绝结果与 call_id 配对，剩余步数内可继续 |
| 一轮包含允许与拒绝的多个调用 | 每条都有独立结果，拒绝的 handler 调用零次 |
| M4c 非交互 `-p` 需要确认 | 不等待输入、不自动授权，生成拒绝观察 |
| 可选审查器不可用 | 不把缺少审查结论当成批准 |

OS 沙箱需另做真实文件/网络边界实验。通过上表的字符串识别测试不能证明任意 Bash 越界已被阻止。

## 9. 本次实际完成的验证与未完成项

- 已读取固定版本的公开源码与官方文档；本机只读运行 Codex 规则检查器，并做 Python shlex 分词实验。
- 未执行文档中的破坏性命令，未运行上述产品的任务或模型审批流程，未安装或切换任何 Agent 配置。
- 未构建并运行整个 Codex/OpenCode，也未实际运行 Claude/Gemini；源码中的测试与注释作为证据，与本次执行的实验分别标注。
- 原调研未修改核心实现、测试或依赖；后续基础流程已有实现。本文的解析建议仍需先声明支持范围与失败处理，再补相应行为测试，不能用旧调研的“下一步”覆盖现行计划。

## 10. 一手来源索引

以下源码链接固定到本次读取的提交；文档为 2026-10-18 实际读取页面。本文所述产品机制只在已注明范围内成立，不以“没有查到”证明某能力不存在。

### Codex

[C-rules]: https://learn.chatgpt.com/docs/agent-configuration/rules
[C-engine]: https://github.com/openai/codex/blob/8c076942cb70fba8e5c4732c23f1faabc4aed484/codex-rs/execpolicy/README.md
[C-decision]: https://github.com/openai/codex/blob/8c076942cb70fba8e5c4732c23f1faabc4aed484/codex-rs/execpolicy/src/decision.rs#L7-L26
[C-parse]: https://github.com/openai/codex/blob/8c076942cb70fba8e5c4732c23f1faabc4aed484/codex-rs/shell-command/src/bash.rs#L21-L157
[C-danger]: https://github.com/openai/codex/blob/8c076942cb70fba8e5c4732c23f1faabc4aed484/codex-rs/shell-command/src/command_safety/is_dangerous_command.rs
[C-route]: https://github.com/openai/codex/blob/8c076942cb70fba8e5c4732c23f1faabc4aed484/codex-rs/core/src/exec_policy.rs#L339-L456
[C-auto]: https://learn.chatgpt.com/docs/sandboxing/auto-review
[C-assessment]: https://github.com/openai/codex/blob/8c076942cb70fba8e5c4732c23f1faabc4aed484/codex-rs/ext/guardian-reviewer/src/assessment.rs#L11-L93
[C-approvals]: https://learn.chatgpt.com/docs/agent-approvals-security
[C-sandbox]: https://learn.chatgpt.com/docs/sandboxing

- [Rules][C-rules]：规则字段、shell wrapper 与复合命令、检查命令。
- [execpolicy README][C-engine] / [Decision][C-decision]：规则引擎与决策顺序；独立检查器的 no-match 结果。
- [bash.rs][C-parse]：完整受限解析与危险字面片段提取两条路径。
- [危险启发式][C-danger]：forced rm、包装与相关源码测试。
- [核心执行策略][C-route]：规则汇总与审批路由；同文件 L778–910 另见未命中规则时的政策及 wrapper 回退。
- [Auto-review][C-auto] / [结构化评估][C-assessment] / [审批与隔离][C-approvals] / [Sandbox][C-sandbox]：独立模型审批和执行环境的职责。

### Claude Code、Gemini 与语言/命令文档

[A-permissions]: https://code.claude.com/docs/en/permissions
[A-modes]: https://code.claude.com/docs/en/permission-modes#how-auto-mode-evaluates-actions
[A-auto-config]: https://code.claude.com/docs/en/auto-mode-config
[A-engineering]: https://www.anthropic.com/engineering/claude-code-auto-mode
[A-hooks]: https://code.claude.com/docs/en/hooks#pretooluse-decision-control
[A-sandbox]: https://code.claude.com/docs/en/sandboxing
[G-policy]: https://geminicli.com/docs/reference/policy-engine/
[P-shlex]: https://docs.python.org/3.12/library/shlex.html
[Git-reset]: https://git-scm.com/docs/git-reset
[Git-clean]: https://git-scm.com/docs/git-clean

- [Claude 权限][A-permissions]、[权限模式][A-modes]、[auto mode 配置][A-auto-config]：规则与模型分类器的分工及覆盖范围。
- [Anthropic 工程说明][A-engineering]：官方标注发布于 2026-03-25，介绍模型审批设计和局限。
- [Hooks][A-hooks]、[Sandbox][A-sandbox]：执行前扩展与 OS 边界。
- [Gemini Policy engine][G-policy]：规则条件、决策和优先级。
- [Python shlex][P-shlex]、[Git reset][Git-reset]、[Git clean][Git-clean]：词法分析范围和具体命令语义。
