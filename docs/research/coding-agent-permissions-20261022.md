# 编程 Agent 的 Shell 权限管控调查

研究日期：2026-10-22。对象：ZCode、OpenCode、Codex、mini-swe-agent、Claude Code。

本文回答：这些工具怎样分析 Bash/Shell 命令，怎样决定允许、拒绝或确认，以及执行环境如何限制实际副作用。ZCode 指智谱/Z.ai 的产品；Claude 指 Claude Code。本次研究用于 `code_agent` 的设计讨论，不修改 [PLAN.md](../../PLAN.md) 的阶段状态、实现范围或依赖。

## 阅读顺序

1. 先读「对照与结论」，了解五种方案的差异。
2. 再读各工具小节，区分文档承诺、源码实现与未核实项。
3. 最后读「对 code_agent 的建议」和测试矩阵，选择实际要实现的边界。

## 1. 分析框架与证据规则

### 1.1 权限管控至少包含五层

| 层次 | 要回答的问题 | 典型机制 | 不能单独证明什么 |
| --- | --- | --- | --- |
| 参数校验 | 工具输入是否合法？ | JSON/schema、类型、取值范围 | 合法字符串仍可执行任意程序 |
| Shell 结构分析 | 哪些部分是命令、参数、重定向或嵌套执行？ | 语法树、受限 scanner | 不能确定任意脚本的完整副作用 |
| 权限规则 | 本次操作是否命中预先授予或禁止的范围？ | argv 前缀、文本通配、路径规则 | 无匹配不等于已授权 |
| 审批 | 这次具体动作是否符合任务与授权？ | 人工、独立模型审查 | 批准可能出错，不构成隔离 |
| 执行约束 | 进程实际能访问哪些文件、网络和凭据？ | OS 沙箱、容器、受控执行环境 | 被授权范围内仍可能发生错误修改 |

这是本文用于比较的分析框架，不表示每个产品都实现全部层次或按同一固定顺序运行。

```mermaid
flowchart LR
    A[工具输入与可信执行环境] --> B[参数校验]
    B --> C[命令结构与已知目标分析]
    C --> D[权限规则与任务授权]
    D -->|allow| F[在实际执行边界内运行原始操作]
    D -->|ask| E[人工或独立模型审批]
    E -->|批准| F
    D -->|deny| G[不执行并给出原因]
    E -->|拒绝或未取得批准| G
```

### 1.2 不混淆三个“完整”

- **语法解析完整**：识别了所声明支持的语法结构。
- **静态参数可确定**：无需执行展开即可得到实际命令名、参数或目标。
- **副作用可确定**：知道程序及其加载代码最终会访问什么资源。

前者不推出后者。例如 `python task.py` 的外层结构很简单，但脚本可以执行文件、网络和子进程操作。以下建议与示例是设计分析，不是对五个产品所有版本的实测预测。

### 1.3 证据等级

- **文档**：官方描述的产品行为；可能随版本、模式、平台变化。
- **源码**：指定提交中可定位的实现；不自动等于当前已发布安装包。
- **本机实验**：本文明确记录版本、输入和结果的静态规则实验。
- **推论/建议**：根据前述证据提出的工程选择，明确标注，不冒充产品事实。

仅凭没有找到资料，不能断言某产品没有某能力。本次没有安装或运行五个产品的完整 Agent 任务，也没有执行文中的破坏性示例。

## 2. 对照与结论

### 2.1 核心差异

| 工具与本次范围 | 命令分析 | 授权与自动行为 | 实际执行边界 |
| --- | --- | --- | --- |
| ZCode，固定 main | unbash AST + 命令级只读参数分析 | 项目规则、工具能力与模式组合；普通 yolo 路径会跳过部分常规规则；guarded 识别出的危险命令单次人工确认 | 官方说明共享执行适配器默认无 OS 沙箱 |
| OpenCode，固定 dev | Tree-sitter 或自建 portable scanner | 提取命令文本后通配匹配；同资源最后匹配胜出；auto 批准 ask | 官方明确无 Agent 沙箱 |
| Codex，官方文档 + 本机规则实验 | 对受限线性字面脚本做 Tree-sitter 拆分 | argv 前缀规则取最严格；可把符合条件的审批交给独立 reviewer | 文件/网络沙箱或 permission profile，依平台与有效配置 |
| mini 默认交互 CLI | 权限关卡不拆解 Bash，正则匹配完整 command | confirm 模式批次人工确认；yolo 跳过；DefaultAgent 本身无确认关卡 | local 无隔离，可显式选择其他 environment |
| Claude Code，官方文档 | 已披露复合/嵌套命令、包装器和重定向检查；完整 Bash parser 未公开 | deny > ask > allow，模式有额外语义；auto 有独立 classifier | 可启用 Shell 沙箱；产品原生 Windows 仍无此隔离 |

各行依据及限制见对应产品小节。**“auto”不是统一概念：OpenCode 的自动批准、ZCode 的内部保留模式、Codex 的独立审批代理、Claude 的 classifier 是不同机制。**

对于用户提出的“详细分析需要拆解命令”，调查支持的结论是：结构分析能减少把引用文本、复合操作和重定向混为一谈的错误，但成熟工具也没有统一的“解析后就能证明任意脚本安全”方案。mini 的默认权限设计甚至保留了整串正则与人工确认的简单路线。

### 2.2 版本与复核范围

| 对象 | 证据固定方式 | 本次实际做了什么 |
| --- | --- | --- |
| ZCode | `3c5b227649371507ddc4eb46ac5133afe6db1cab` | 官方 main 源码、仓库设计文档与产品文档 |
| OpenCode | `c93aaae37157d785f76a8e9a9243a6560e286468` | 官方 dev 源码、上游测试文本与产品文档；未运行上游测试 |
| mini-swe-agent | `04d809ceab9df28f9adaed044884180159172930` | 官方 main 的 CLI、Agent、local/Docker 环境与配置 |
| Codex | 调查日官方文档；本机 `0.163.0-alpha.11` | 8 例独立 execpolicy 静态检查；未实测完整执行链 |
| Claude Code | 调查日官方页面及其中明确版本条件 | 文档和官方设计披露；未审计完整闭源实现 |

三份开源快照的 SHA 均在本次通过官方 GitHub API 获取；后文源码链接固定到相应 SHA。没有因为某工具开源就假定其公开 main/dev 与所有发布包一致。

## 3. ZCode

证据：智谱/Z.ai 官方 [ZCode 仓库](https://github.com/zai-org/ZCode)，固定 main 提交 [`3c5b227649371507ddc4eb46ac5133afe6db1cab`](https://github.com/zai-org/ZCode/commit/3c5b227649371507ddc4eb46ac5133afe6db1cab)。仓库含桌面、服务端、Agent CLI/runtime；下文分别讨论共享核心和独立 CLI，不以桌面按钮名称推断所有入口。

### 3.1 AST、只读识别和权限规则分别负责一部分

实现使用纯 TypeScript 的 `unbash` 同步 parser，输出 invocation、参数、环境赋值、重定向、连接操作符和动态/错误标记。支持的结构会被遍历；动态展开、不支持节点、解析错误等阻止进入对应的静态自动批准路径，不执行命令来求值。[解析器](https://github.com/zai-org/ZCode/blob/3c5b227649371507ddc4eb46ac5133afe6db1cab/apps/zcode-cli/packages/core/src/tool/handlers/bash-command-parser.ts#L1-L110)、[结构遍历](https://github.com/zai-org/ZCode/blob/3c5b227649371507ddc4eb46ac5133afe6db1cab/apps/zcode-cli/packages/core/src/tool/handlers/bash-command-parser.ts#L129-L289)

只读分析还检查具体选项、重定向、wrapper 和上下文，例如 `find -exec/-delete`、`sed -i` 等不能仅因命令名在集合内就当只读。Bash 默认的高风险能力描述可以在满足只读规则时由运行时能力覆盖。[只读参数分析](https://github.com/zai-org/ZCode/blob/3c5b227649371507ddc4eb46ac5133afe6db1cab/apps/zcode-cli/packages/core/src/tool/handlers/bash-readonly-policy-argv.ts)、[能力解析](https://github.com/zai-org/ZCode/blob/3c5b227649371507ddc4eb46ac5133afe6db1cab/apps/zcode-cli/packages/core/src/tool/handlers/bash.ts#L114-L140)

仓库明确把“当次是否只读”和“持续授权什么前缀”分开。前缀建议使用命令注册表与已知 action 归纳，不是给该命令颁发只读证明。[官方仓库语义说明](https://github.com/zai-org/ZCode/blob/3c5b227649371507ddc4eb46ac5133afe6db1cab/apps/zcode-cli/docs/bash-tool-ast-permission-semantics.md)

### 3.2 复合命令的规则覆盖

专属评估器先检查整条原始命令的 exact 规则；满足受限静态结构条件时，再按 invocation 做 token 边界 prefix / wildcard 匹配：allow 需覆盖所有需要审批的 invocation，deny/ask 任一命中即可。动态或不完整分析不走这种 prefix 自动批准，但整串 exact、工具级规则和模式仍可能改变最终决定，故不能称为“解析失败必定 deny”。[规则评估器](https://github.com/zai-org/ZCode/blob/3c5b227649371507ddc4eb46ac5133afe6db1cab/apps/zcode-cli/packages/core/src/tool/handlers/bash-command-rule-evaluator.ts)

这与仅匹配 `npm run` 开头不同：第一段批准不能自动覆盖后附的另一个非只读调用。无法稳定生成持续授权建议时，可回退整条 exact。[授权建议](https://github.com/zai-org/ZCode/blob/3c5b227649371507ddc4eb46ac5133afe6db1cab/apps/zcode-cli/packages/core/src/tool/handlers/bash-command-permission-policy.ts#L99)

### 3.3 模式优先级不能套用别的产品

| 模式/入口 | 固定源码中的关键行为 |
| --- | --- |
| build / edit | 普通路径先处理项目 deny、ask，再经过 Plan 约束和 allow；edit 另允许工作区编辑类能力 |
| plan | 应用层限制写入，不等于 OS 只读沙箱 |
| yolo | 普通调用的基础放行分支早于 disallowedTools 和常规项目 deny/ask |
| guarded | 普通调用采用 yolo 基础策略；识别出的危险命令另走 user-once 人工确认 |
| auto | 内部保留，当前未实现，不能称为现成模型审批 |
| 共享配置 / 独立 CLI | 共享默认 build；独立 `--prompt` 未指定 mode 时默认 yolo |

前述是所读快照的顺序，不建议用户复制为本项目规则。需要交互的工具、alwaysAsk、独立 Plan 和 guarded user-once 等有更早特例，不能把“yolo 直通”推广到所有请求，也不能反过来说“deny 在所有模式永远胜出”。[服务判定顺序](https://github.com/zai-org/ZCode/blob/3c5b227649371507ddc4eb46ac5133afe6db1cab/apps/zcode-cli/packages/core/src/permission/service.ts#L116-L215)、[alwaysAsk 特例](https://github.com/zai-org/ZCode/blob/3c5b227649371507ddc4eb46ac5133afe6db1cab/apps/zcode-cli/packages/core/src/permission/service.ts#L357-L426)、[CLI 权限指南](https://github.com/zai-org/ZCode/blob/3c5b227649371507ddc4eb46ac5133afe6db1cab/apps/zcode-cli/docs/usage/05-permissions.md)

### 3.4 审批、不可用和执行边界

普通 ask 交给 Permission Broker/客户端，PermissionRequest Hook 也能应答。guarded 危险请求限定用户单次批准，不接受该 Hook 代批或持续授权建议；修改工具输入还要重新准备并按对应路径复查。本次未在内置调用链发现独立模型权限裁判。[权限流](https://github.com/zai-org/ZCode/blob/3c5b227649371507ddc4eb46ac5133afe6db1cab/apps/zcode-cli/packages/core/src/tool/executor/permission-flow.ts#L93-L237)、[单次应答校验](https://github.com/zai-org/ZCode/blob/3c5b227649371507ddc4eb46ac5133afe6db1cab/apps/zcode-cli/packages/core/src/tool/executor/permission-response.ts#L99)

ask 却没有审批客户端会失败；Hook 失败后继续等客户端，不默认批准。审批取消/异常会关闭请求并形成工具错误；CLI 最后退出 0 仍不代表工具已获批准。[CLI 指南](https://github.com/zai-org/ZCode/blob/3c5b227649371507ddc4eb46ac5133afe6db1cab/apps/zcode-cli/docs/usage/05-permissions.md)、[请求处理](https://github.com/zai-org/ZCode/blob/3c5b227649371507ddc4eb46ac5133afe6db1cab/apps/zcode-cli/packages/core/src/tool/executor/permission-request.ts#L34-L118)

官方 NOTICE 说明共享执行适配器默认无 OS 沙箱；工作目录、worktree 和页面隔离不是所有工具的系统隔离保证。Windows 可用 Git Bash/CMD 等 provider，方言不同，不能把 POSIX 分析能力推定为 CMD 完整语义覆盖。[NOTICE](https://github.com/zai-org/ZCode/blob/3c5b227649371507ddc4eb46ac5133afe6db1cab/NOTICE.md#L9)、[Shell provider](https://github.com/zai-org/ZCode/blob/3c5b227649371507ddc4eb46ac5133afe6db1cab/apps/zcode-cli/docs/bash-shell-provider.md)

未核实完整危险规则库的覆盖率、所有方言语义一致性、公开源码与发布包的一致性及运行时竞态；没有做真实绕过或隔离实验。

## 4. OpenCode

证据：官方文档与本次固定的 `dev` 提交 [`c93aaae37157d785f76a8e9a9243a6560e286468`](https://github.com/anomalyco/opencode/commit/c93aaae37157d785f76a8e9a9243a6560e286468)，提交时间 2026-10-22 04:54:59 UTC。开发快照不等于任意安装包。

### 4.1 调用链：解析资源后统一授权

当前工具叫 `shell`。执行前解析 cwd，扫描完整命令，对工作目录和已识别目录切换目标做 `external_directory` 检查，再把各命令的 `resource` 交给统一权限系统。审批后重查 cwd，最终执行原始完整命令。[prepare](https://github.com/anomalyco/opencode/blob/c93aaae37157d785f76a8e9a9243a6560e286468/packages/core/src/tool/plugin/shell.ts#L117-L155)、[执行入口](https://github.com/anomalyco/opencode/blob/c93aaae37157d785f76a8e9a9243a6560e286468/packages/core/src/tool/plugin/shell.ts#L199-L219)

### 4.2 两条解析路径

| 路径 | 实现 | 失败与选择条件 |
| --- | --- | --- |
| Legacy | `web-tree-sitter` 遍历 Bash/PowerShell 树中的 command；直接位于重定向节点下时保留父节点文本 | 无树时报错；所读入口没有统一检查整棵树的 `hasError` |
| Portable | 自建结构扫描器，处理引号、操作符、分组、重定向和多种嵌套结构 | `opaque` 导致工具错误；受配置、channel、WASM 资源影响 |

来源：[路径选择](https://github.com/anomalyco/opencode/blob/c93aaae37157d785f76a8e9a9243a6560e286468/packages/core/src/tool/plugin/shell.ts#L126-L129)、[解析适配](https://github.com/anomalyco/opencode/blob/c93aaae37157d785f76a8e9a9243a6560e286468/packages/core/src/shell/parse.ts#L164-L287)。Portable 不是简单分词器：它支持递归扫描部分命令/进程替换，并设置长度、深度和工作量边界；不能把所有 `$()` 都说成不支持。[扫描器](https://github.com/anomalyco/opencode/blob/c93aaae37157d785f76a8e9a9243a6560e286468/packages/core/src/shell/scan.ts#L3-L90)、[嵌套扫描](https://github.com/anomalyco/opencode/blob/c93aaae37157d785f76a8e9a9243a6560e286468/packages/core/src/shell/scan.ts#L1236-L1283)

适配层主要生成 `commands: [{resource, save}]` 和部分目录目标。`resource` 是本次匹配的命令文本，`save` 是建议保存的前缀模式；命令 arity 表决定前缀取几个词。这不是完整的文件读写/联网效果模型。[结果与前缀](https://github.com/anomalyco/opencode/blob/c93aaae37157d785f76a8e9a9243a6560e286468/packages/core/src/shell/parse.ts#L18-L85)、[prefix 算法](https://github.com/anomalyco/opencode/blob/c93aaae37157d785f76a8e9a9243a6560e286468/packages/core/src/shell/parse.ts#L375-L380)

### 4.3 规则、批准和非交互行为

- 同一 action/resource **最后匹配规则胜出**，无匹配 fallback ask；这不是无条件的 deny 优先。多资源先检查配置判决，有一个 deny 就拒绝整次请求，否则再合入保存批准并处理 ask。[规则与聚合](https://github.com/anomalyco/opencode/blob/c93aaae37157d785f76a8e9a9243a6560e286468/packages/core/src/permission.ts#L74-L88)、[配置 deny 与保存批准](https://github.com/anomalyco/opencode/blob/c93aaae37157d785f76a8e9a9243a6560e286468/packages/core/src/permission.ts#L145-L176)
- 人工可 once、always、reject。**文档/源码差异**：官方页面说 always 持续到当前会话结束；本次开发源码按项目写 SQLite 并重新加载，读取链未限制 session。不能据此替用户推断其安装版本的行为。[文档](https://opencode.ai/docs/permissions/#what-ask-does)、[持久批准](https://github.com/anomalyco/opencode/blob/c93aaae37157d785f76a8e9a9243a6560e286468/packages/core/src/permission/saved.ts#L59-L84)
- `--auto` 自动批准 ask，不是启动模型风险分类器；显式 deny 仍有效。非交互 run 对 ask 默认 reject，`--auto` 则回复 once；新协议附反馈使模型可另寻做法。[官方说明](https://opencode.ai/docs/permissions/#auto-mode)、[非交互源码](https://github.com/anomalyco/opencode/blob/c93aaae37157d785f76a8e9a9243a6560e286468/packages/cli/src/run/noninteractive.ts#L158-L185)

底层 fallback 与产品默认配置分开：文档称大多数权限默认 allow，外部目录和重复调用保护默认 ask。[默认值](https://opencode.ai/docs/permissions/#defaults)。本次内置调用链未发现独立模型审查；插件 hook 可改变行为，因此不能推广到所有第三方配置。

### 4.4 隔离与覆盖边界

官方明确不提供 Agent 沙箱，permission 用于告知与确认操作，不构成安全隔离。[SECURITY.md](https://github.com/anomalyco/opencode/blob/c93aaae37157d785f76a8e9a9243a6560e286468/SECURITY.md#L11-L19)

一个有代表性的**静态边界**：上游测试把 `> /dev/null`、`X=x 2> output` 等纯重定向列为 `commands: []`；而 prepare 只对非空 commands 做 shell 权限 assert。可推断这个关卡围绕“命令资源”，未为所有纯重定向建立独立资源。本次未实测产品表现，不把它称为已验证绕过或漏洞。[上游测试](https://github.com/anomalyco/opencode/blob/c93aaae37157d785f76a8e9a9243a6560e286468/packages/core/test/shell-scan/bash-safety.test.ts#L84-L108)、[非空条件](https://github.com/anomalyco/opencode/blob/c93aaae37157d785f76a8e9a9243a6560e286468/packages/core/src/tool/plugin/shell.ts#L136-L145)

## 5. Codex

证据范围：本次实际读取的 OpenAI 官方文档，以及本机 `codex-cli 0.163.0-alpha.11` 的独立规则检查器实验。文档与本机 alpha 版本分别记录；本节没有核验当前 Codex 仓库源码，不把文档说明当作本机所有路径的实测结果。

### 5.1 Shell 拆解采用受限的语法分析

官方 Rules 文档描述：对由字面参数和 `&&`、`||`、`;`、`|` 组成的受支持线性脚本，使用 Tree-sitter 拆分后逐条应用规则。含变量、赋值、重定向、替换、glob 或控制流时，不沿用这条拆分路径，而按整个 Shell invocation 处理。不能拆分不代表最终必然允许或拒绝，仍需结合执行政策。[规则与 Shell wrapper](https://learn.chatgpt.com/docs/agent-configuration/rules#shell-wrappers-and-compound-commands)

### 5.2 规则匹配结构化 argv 前缀

`prefix_rule` 的 pattern 是参数列表，决定为 `allow / prompt / forbidden`；多条命中取最严格结果，即 `forbidden > prompt > allow`。规则可附加载时验证的正反样例。官方把这组规则定位为控制沙箱外命令执行，不能据此推导所有工具或全部沙箱内动作都一定走同一审查路径。[Rules](https://learn.chatgpt.com/docs/agent-configuration/rules)

### 5.3 审批政策、审查者与沙箱分别配置

- 审批政策决定何时出现审批请求；常见政策包括 `on-request` 和 `never`。`never` 表示不询问，不等于授予全部权限。[Sandbox](https://learn.chatgpt.com/docs/sandboxing)
- `approvals_reviewer = "auto_review"` 可把符合条件的审批交给独立 reviewer；普通沙箱内已允许的动作不因此全部再审一次。拒绝与超时分开呈现，超时本身不构成不安全的证明。审查者选择不取消组织约束。[Auto-review](https://learn.chatgpt.com/docs/sandboxing/auto-review)
- 文件和网络能力由执行环境限制；新 permission profiles 与旧 `sandbox_mode` 配置存在选择和兼容规则，不应混搭后假定叠加。域名规则还依赖实际启用的网络代理，配置目标列表不自动产生出口控制。[Permission profiles](https://learn.chatgpt.com/docs/permissions)

当前 Windows 官方文档区分 MXC、elevated、unelevated，能力和设备要求不同，不能只写“Windows 有沙箱”而省略实现条件。文档称限制覆盖命令及其子进程；这不是对本机所选实现的隔离实测。[Windows sandbox](https://learn.chatgpt.com/docs/windows/windows-sandbox)

### 5.4 本次本机静态实验

实验创建独立临时规则：`git` 前缀 allow、`git reset --hard` 前缀 forbidden、`git clean` 前缀 prompt。宽 `git` allow 只为观察匹配行为，不是推荐授权。

| 输入 argv | 检查器返回 |
| --- | --- |
| `git status` | allow |
| `git reset --hard` | forbidden |
| `git clean -fd` | prompt |
| `git -C . reset --hard` | allow，只命中宽 `git` 规则 |
| `/usr/bin/git reset --hard` | 无匹配，无 decision 字段 |
| `python task.py` | 无匹配 |
| `echo` 加文字参数 `git reset --hard` | 无匹配 |
| `bash -lc "git status && git reset --hard"` | 无匹配 |

8 项检查器进程均退出 0；这是成功返回判定，不表示被检查命令执行成功。命令字符串仅作为 `codex execpolicy check` 的参数，**没有执行 Git 操作、Python 脚本或内层 Bash**。

最后一例只说明独立规则检查器没有匹配传入的 wrapper argv，不能反驳上层 Agent 的 Shell 拆分机制。`git -C` 一例展示精确前缀与等价参数形式的边界；无匹配不等于完整执行流程最终放行。

本次原始证据位于已忽略的 `artifacts/permission-survey-20261022/codex/`：`check_rules.py`、`experiment.rules`、`results.json`。本节表格为本次新运行结果，没有沿用旧实验报告。

## 6. mini-swe-agent

证据：官方文档与固定 `main` 提交 [`04d809ceab9df28f9adaed044884180159172930`](https://github.com/SWE-agent/mini-swe-agent/commit/04d809ceab9df28f9adaed044884180159172930)，提交时间 2026-09-03 05:05:59 UTC。本节区分交互 CLI、直接调用基础 Agent 和不同 environment。

### 6.1 核心很小：整条正则加批次确认

固定快照的 `mini` CLI 默认选择 local environment、InteractiveAgent，内置配置为 confirm。[CLI](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/run/mini.py#L94-L102)、[配置](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/config/mini.yaml#L101-L110)

确认条件实质是：confirm 模式且完整 command 未被任一个 `re.match(pattern, command)` 白名单匹配。默认白名单为空。**这个权限关卡没有 Bash AST、三态拒绝规则或独立模型裁判**；模式与可选正则决定是否人工确认。直接使用 `DefaultAgent` 则逐条 `env.execute()`，不经过 InteractiveAgent 的确认。[默认配置](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/agents/interactive.py#L24-L30)、[正则检查](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/agents/interactive.py#L162-L182)、[基础执行](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/agents/default.py#L154-L157)

**推论：**只约束开头的宽正则可能同时匹配后附复合操作的字符串；这是输入与 `re.match` 语义导致的配置边界，没有运行绕过实验。

### 6.2 批次批准与模式

一条模型消息可给多个 actions；只要一个需要确认，就在执行任何 action 前一次确认整批。Enter 批准本批，其他评论拒绝并反馈给模型；`/y` 除批准外还切换 yolo，`/u` 拒绝当前批并切到 human。[批次入口](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/agents/interactive.py#L124-L139)、[输入和模式切换](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/agents/interactive.py#L162-L209)

这与本项目“空输入拒绝、单次授权”的计划契约不同，适合借鉴最小确认循环，不宜直接照搬交互语义。

`--yolo` 跳过动作确认，但 `confirm_exit` 独立控制结束确认，需结合 `--exit-immediately` 理解无人值守行为。动作确认处没有统一 isatty→deny 分支；未实测各类 stdin/EOF。不能因为别处存在 isatty 检查，就声称全部确认都对非交互安全降级。[CLI 参数](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/run/mini.py#L55-L82)、[结束确认](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/agents/interactive.py#L144-L160)

### 6.3 执行隔离由 environment 决定

- Local 以 `shell=True` 在宿主运行，继承父环境并叠加配置；没有在该类内实施文件/网络沙箱。未显式绑定 Bash executable，实际 Shell 遵循 Python 的平台行为。[LocalEnvironment](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/environments/local.py#L24-L43)、[Popen](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/environments/local.py#L72-L92)
- Docker 用 `docker exec ... bash -lc`；默认不转发指定宿主环境变量，但 mount、网络、用户和资源约束要看实际配置，不能把“容器”当成已完成所有隔离。[Docker 配置](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/environments/docker.py#L15-L42)、[执行](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/environments/docker.py#L101-L124)

官方还提供其他 environment。它们改变执行边界，不会自动给交互确认增加 Shell 语义分析。[环境列表](https://mini-swe-agent.com/latest/advanced/environments/)

## 7. Claude Code

证据：Claude Code 官方产品文档、Anthropic 公开设计文章和官方开源沙箱组件；没有用第三方镜像或逆向代码补齐闭源实现。本节没有产品实测。

### 7.1 结构检查已经超出简单文本匹配

官方文档说明会处理复合命令、已识别嵌套执行、固定包装器和环境赋值；复合命令的 allow 需覆盖各子命令。重定向目标还应用 Read/Edit 权限，`tee` 写目标也有检查。规则本身仍匹配处理后的命令文本，采用 **deny > ask > allow**；参数重排、可执行文件路径和另一个解释器的字符串等不自动视为等价。[命令处理](https://code.claude.com/docs/en/permissions#compound-commands)、[匹配边界](https://code.claude.com/docs/en/permissions#what-a-bash-rule-doesnt-match)

未获得官方完整 Bash parser 源码，不能断言其底层使用 Tree-sitter 或 bashlex。PowerShell AST 的官方说明也不能代替 Bash 实现证据。

### 7.2 三种“自动”要区分

| 机制 | 实际区别 |
| --- | --- |
| `acceptEdits` | 固定范围自动允许编辑和部分常见文件命令，并非全部 Bash |
| `auto` | 独立 classifier 审查前序机制尚未解决的操作；不是每条命令必定过模型 |
| 沙箱 auto-allow | 在执行边界内减少确认，与模型 classifier 是不同机制 |

Manual（配置值 `default`）、plan、dontAsk、bypassPermissions 另有用途和例外，不能简单排列为统一安全等级。当前文档称 v2.1.283 起交互终端与 VS Code 内置起始模式为 auto，但受设置、策略和服务条件影响；不能再无条件写“默认逐条人工审批”。显式 deny/ask、需用户交互动作和已识别关键路径删除也不能概括为被 bypass 全部绕过。[模式与版本条件](https://code.claude.com/docs/en/permission-modes#available-modes)、[起始模式](https://code.claude.com/docs/en/permission-modes#which-mode-a-session-starts-in)

进入 auto 会暂停已识别的过宽 allow；`autoMode.classifyAllShell` 可让更多 Shell 调用进入 classifier。**官方披露设计**在 2026-03-25 描述过快速判断与被标记动作的进一步推理，并强调用户授权和实际影响。这是当时披露，不能据此固定当前所有请求的模型、提示或调用次数。[auto 配置](https://code.claude.com/docs/en/auto-mode-config#route-all-shell-commands-through-the-classifier)、[设计文章](https://www.anthropic.com/research/claude-code-auto-mode)

### 7.3 不同失败有不同语义

| 情况 | 本次核实的行为 |
| --- | --- |
| Manual 无法完整分析 | 常规权限路径要求批准；沙箱 auto-allow 有例外，局部 allow 不能直接证明整体可放行 |
| classifier 无有效判决 | 拒绝本次动作，不默认允许 |
| 非交互 `claude -p` 缺少审批宿主 | 需要确认的调用被拒绝，不能假定能回退终端询问 |
| hook 失败或超时 | 多数默认返回正常权限流程；可用 `onFailure: "block"` 表达强制阻断 |
| 沙箱启动不可用 | 默认可能继续无沙箱运行；可配置 `failIfUnavailable` 要求退出 |

来源：[命令分析](https://code.claude.com/docs/en/security#additional-safeguards)、[classifier 与非交互](https://code.claude.com/docs/en/permission-modes#when-auto-mode-falls-back)、[hooks](https://code.claude.com/docs/en/hooks#block-the-action-when-a-hook-fails)、[沙箱启动](https://code.claude.com/docs/en/sandboxing#get-started)。这些是模式相关行为，不能总结为“所有错误一律拒绝”或“出错都继续”。

SDK 的 `canUseTool` 是尚未被前序授权机制解决时的回调，并非每次调用必经的硬门禁；文档给出 hooks、deny、ask、mode、allow、callback 等顺序，但不应把 SDK 顺序机械推广到所有 CLI/服务器路径。[SDK 权限求值](https://code.claude.com/docs/en/agent-sdk/permissions#how-permissions-are-evaluated)

### 7.4 沙箱是单独且有范围的能力

产品文档称 Bash 沙箱默认关闭，支持 macOS、Linux、WSL2；原生 Windows 命令仍无这项内置隔离。启用后限制 Shell 及子进程的文件和网络访问，Read/Edit、MCP、hooks 等不自动位于这个沙箱中。读权限默认也不等于“仅项目可读”。[沙箱范围](https://code.claude.com/docs/en/sandboxing#what-the-sandbox-restricts)

默认存在请求 unsandboxed retry 的路径，可用 `allowUnsandboxedCommands=false` 收紧，并需检查 excludedCommands。官方 [sandbox-runtime](https://github.com/anthropics/sandbox-runtime) 组件已有 Windows alpha，不代表 Claude Code 产品已经接入；产品支持范围仍以上述文档为准。[沙箱模式与重试](https://code.claude.com/docs/en/sandboxing#sandbox-modes)

## 8. 共同难点与对 code_agent 的建议

### 8.1 命令分析必须保留的边界

以下是根据语言行为与本次产品机制提出的测试问题，**不是声称五个产品会给出同一判决**。

| 输入/变化 | 分析器或策略需要回答什么 |
| --- | --- |
| `echo '$(python task.py)'` / `echo "$(python task.py)"` | 区分字面文本和实际嵌套执行 |
| `cat a.txt > b.txt` | 重定向产生的写操作不能遗漏 |
| `cd subdir && cat a.txt` | 后续相对路径的解释依赖前序状态 |
| `(cd subdir; cat a.txt); cat b.txt` | 子 Shell 状态不能错误传播到外层 |
| `git status && another_command` | 必须覆盖整次调用，不能只检查第一条 |
| `git -C repo reset --hard` | 全局选项使子命令位置改变，宽前缀不能当完整语义规则 |
| `python task.py`、`pytest`、`npm run build` | Shell 结构简单不代表加载代码副作用已知 |
| `$CMD "$TARGET"`、glob、`eval`、`source` | 动态值或加载代码无法确定时必须记录未知 |
| PATH、函数、环境变量、符号链接变化 | 审核的名字或路径可能与实际执行对象不同 |
| 审批期间命令或文件变化 | 原始参数绑定与文件内容不可变是两种保证 |

Shell 的命令替换会执行内层命令，重定向本身具有文件访问效果。[Bash 命令替换](https://www.gnu.org/s/bash/manual/html_node/Command-Substitution.html)、[重定向](https://www.gnu.org/s/bash/manual/html_node/Redirections.html)。上表其余条目是据此扩展的工程验收建议，不宣称仅靠语法解析即可解决。

### 8.2 推荐的职责划分

这是对本项目的设计建议，不是新接口或已批准的实施规格。

| 位置 | 建议职责 |
| --- | --- |
| 工具注册与参数层 | 查找工具、校验并固定实际参数；进入 handler 前经过检查接点 |
| Shell 分析器 | 只分析，不执行；保留结构、原始片段、确定信息和未知原因 |
| safety | 结合分析结果、宿主范围和真实用户授权产生三态决定与理由 |
| core | 连接审批、执行和历史；保证批准的是随后执行的实际操作 |
| UI | 显示具体动作，采集批准或拒绝，正确处理 EOF 和中断 |
| 执行环境 | 独立落实文件、网络和凭据边界；进程清理不等于访问隔离 |

当前实现中，`safety/policy.py` 只解析执行环境，`ToolRegistry.execute` 尚无权限检查；`ui/prompts.py` 仍是占位。已有取消与历史结算需要保留，审批期间取消应记为 `not_started`，不能沿用进入 Registry 前设置的执行中标记。

### 8.3 建议的小步顺序

1. **先完成纯分析实验。** 使用现成 Bash parser，声明支持的结构；输出已知命令、重定向、上下文关系与未知项。不要先自写完整 Shell 解释器。
2. **建立审批入口。** 用假策略和假确认验证 allow、ask、deny；未支持结构不自动放行。解析失败、分析不完整、策略拒绝使用不同原因。
3. **再减少无价值确认。** 从少量可解释规则开始；可独立比较人工与模型审查，记录误放、误拦和延迟。不要同时上完整规则 DSL、永久授权和复杂自动裁判。
4. **独立验证执行边界。** 若要宣称不能越界读写或联网，需要真实环境实验，不能用字符串识别测试替代。

拆解只用于检查；审批后保持原始命令及执行环境一致，不把拆出的命令分别重放。全局决定应覆盖全部潜在执行部分；部分识别可用于发现禁止动作，不能仅凭已识别部分全部允许就证明整条调用可放行。

既有 [M4c 草案](../design/m4c-permissions.md) 提议每次 Bash 使用独立模型审查，这是候选设计；[有效计划](../../PLAN.md) 仍把独立模型审批列为可选扩展。本文不把完整 AST、裁判模型或强沙箱自动增加为 M4c 前置条件。

### 8.4 权限执行链的最小验证矩阵

| 场景 | 本项目应独立验证的结果 |
| --- | --- |
| 参数非法、未知工具 | 策略与 handler 均零次；保留既有错误语义 |
| 规则允许 | handler 一次，参数与检查快照一致 |
| 明确拒绝、人工拒绝 | handler 零次，理由与 tool_call_id 配对 |
| ask 后批准 | 批准发生在执行前，只授权当前操作 |
| `-p` 或非交互遇到 ask | 不等待 stdin，不自动批准，生成未执行观察 |
| 解析错误、覆盖不足 | 明确分类；不能把缺少结论当 allow |
| 独立审查请求失败或结果非法 | handler 零次；区分不可用与明确拒绝 |
| 审查或确认期间取消 | 当前及后续未执行调用 not_started，已知观察不变 |
| handler 执行中取消 | 沿用已有清理及 unknown/已知结果，不自动重跑 |
| 确认取消后继续输入 | 原读取已结束，不留下抢占下一行的后台读取者 |

危险示例只送给分析器；执行链集成测试使用假 handler 计数。真实文件/网络限制另在独立靶子验证。

## 9. 研究限制与复核方式

- 官方文档、固定源码与本机版本可能不同步；出现差异时按各自证据范围解释。
- 本次本机实验只覆盖 Codex 独立规则检查器，未验证完整 Agent、模型判决准确率或 OS 隔离。
- 没有执行本文的破坏性示例，没有修改任何产品权限配置或真实用户凭据。
- 仅调整研究文档；没有修改本项目运行代码、依赖或阶段验收状态。
- 产品小节保留一手来源链接；复核开源实现时先定位记录的提交，再检查调用入口、解析器、规则汇总和执行接点，不从单个辅助函数推断整条执行链。
