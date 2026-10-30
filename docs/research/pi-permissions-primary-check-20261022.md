# Pi 权限设计：原始来源重点核查

查询日期：2026-10-22（Asia/Shanghai）。输入为 [Pi 研究笔记](pi-agent-research-deepseek.md)；只核查与 [M4c 草案](../design/m4c-permissions.md) 直接相关的三组说法。未安装扩展或验证其运行安全性。

## 1. 已核实：统一执行接点与扩展职责

- `badlogic/pi-mono` 当前重定向至 `earendil-works/pi`；以下链接使用当前官方仓库。
- agent-core 提供异步 `beforeToolCall(context, signal?)`，输入包含原始调用、已校验参数和 Agent 上下文；返回 `{ block: true, reason }` 阻止工具执行，循环生成错误工具结果。接点位于参数校验后，工具实际执行前；`tool_execution_start` 事件会更早发出，不能据该事件宣称进程已启动。见 [agent-core 类型](https://github.com/earendil-works/pi/blob/main/packages/agent/src/types.ts#L54-L108) 与 [执行顺序说明](https://github.com/earendil-works/pi/blob/main/packages/agent/README.md#L99-L108)。
- coding-agent 的 `AgentSession` 把该接点连接到扩展的 `tool_call`，扩展收到 `toolName`、`toolCallId`、`input`，可返回阻止决定；具体策略和确认 UI 可以由扩展提供。受支持的嵌套 `ctx.executeTool()` 也重新经过接点。见 [AgentSession 接线](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/agent-session.ts#L609-L722)。
- Pi 的扩展允许原地修改 `event.input`；这不是不可变审批契约。扩展还以 Pi 进程的 OS 权限运行，接点并不限制扩展任意发起的 I/O。见 [扩展返回类型](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/extensions/types.ts#L1351-L1359) 与 [扩展权限说明](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/extensions.md#L1-L5)。

对本项目的推论：统一执行前接点有利于核查覆盖范围。当前已由 core 连接 `approval/` 的策略、Gate 和确认输入，Registry 在参数校验后调用接点。审批参数与执行参数一致性属于本项目自己的契约，不能从 Pi 的可变参数 hook 直接推出。

## 2. 已核实到文档层：模式、边界和网络分别控制

Pi 官方包目录保存的 `qings-pi-modes` 1.1.0 README 明确列出三条独立轴：审批自动化模式、工作目录外读写的三态沙箱、网络过滤。README 中 `plan / ask / edits / auto / accept` 是该扩展的策略预设；`auto` 使用 AI 分类器，`accept` 自动接受策略决定但仍受沙箱约束。见 [官方包页中的作者 README](https://pi.dev/packages/qings-pi-modes?type=extension)。

该 README 还有两个不能照搬的细节：

- 它的 `plan` 允许项目内 Markdown 写入，并对变更 Bash 询问；不等于本项目草案的纯只读 plan。
- 它明确说明原生 Windows 无该 OS 沙箱，网络 allowlist 不生效，降级为弹窗。不能由模式名称推断当前 Windows/Git Bash 已有隔离能力。

以上均为 [同一作者 README 的文档声明](https://pi.dev/packages/qings-pi-modes?type=extension)，不是本次对实现与隔离效果的独立验证。访问其 [GitHub 仓库](https://github.com/qingshanblue/qings-pi-modes) 返回 404，raw README 也未成功取得，故未核实内部配置合并、网络执行机制及具体白名单代码。

对本项目的推论：把“决策流程/阶段”“审批策略/交互能力”“文件与网络授权”“真实执行后端”分别表达。模式可选默认组合，但不赋予额外资源权限；`auto` 不应等于自动开放网络或忽略宿主禁止项。具体 plan 例外、模式切换和配置交互按后续阶段确定。

## 3. 已核实：AST 的用途与失败分支边界

- Gatekeeper 的作者 README 和 `analyzer.ts` 支持结构化检测输出重定向、遍历复合命令、解析包装命令、检查动态命令名及按参数收紧白名单；解析出的 AST 有语法错误时会门控。这里的门控通常是请求用户批准，不等于最终 `deny`。见 [作者 README](https://github.com/noel-debug/pi-gatekeeper#how-it-works) 与 [分析器代码](https://github.com/noel-debug/pi-gatekeeper/blob/main/analyzer.ts)。
- 必须收窄原笔记的 fail-closed 概括：解析器/WASM 初始化不可用时，该实现回退 `isGatedFallback(command)` 正则匹配；不是所有故障都无条件阻止。因此不能直接把其行为当成本项目的解析失败政策。见 [初始化与 analyzeCommand](https://github.com/noel-debug/pi-gatekeeper/blob/main/analyzer.ts)。
- 作者也声明静态分析并不完美；AST 不证明任意脚本、解释器载荷和运行时副作用安全。[作者的局限说明](https://github.com/noel-debug/pi-gatekeeper#try-to-break-it)
- Bash 本身按引用规则分词，再解析、展开、重定向和执行。因此，原稿的“按操作符拆分”应理解为按 shell 语法遍历，不能落实成普通字符串 `split`；例如 `printf '%s\n' 'a|b'` 中的 `|` 是字面量，`cat file > target` 则含实际写入。原探索稿已据此修订。此结论依据 [Bash 执行流程](https://www.gnu.org/software/bash/manual/html_node/Shell-Operation.html) 和 [引用规则](https://www.gnu.org/software/bash/manual/html_node/Quoting.html)。

## 4. 对 M4c 的建议与未核实项

1. 采用统一执行入口，先校验参数，再综合工具动作与受信任权限上下文；扩展工具也不绕过。
2. 宿主禁止项和模式硬约束先判定；规则、模型审查、普通确认都不能扩大授权。
3. Bash 独立模型审查是草案候选路线，尚未确定采用；当前沿用简单策略贯通流程。AST 可日后提供结构线索，两者均不替代真实隔离。若引入 AST，解析器不可用或不支持语法时明确 ask / deny，不自动照搬正则回退并放行。
4. 无法确定命令符合只读约束时不执行；普通批准不能取消该约束。若允许例外，需要先通过受信任入口明确改变模式/授权。
5. 只借鉴分层与接口扩展能力，不复制扩展的全部预设；真实 AgentMode 仍留在 M6，轨迹仍留在 M5。

原稿中的“覆盖所有结构漏洞”“150–200 行完成”等断言没有验证依据，已从探索笔记移除。`pi-permission-suite`、`pi-ast-guard`、`pi-access-guard` 仍是未核实线索；本页的作者 README 和 main 链接也只是原核查时点的证据，不能作最新实现保证。
