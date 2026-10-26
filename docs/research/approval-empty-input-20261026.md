# 审批时的 Enter、空输入与取消

核查日期：2026-10-26（Asia/Shanghai）。本次实时读取官方文档与源码，未安装或运行这些 Agent。源码链接使用查询时的 `main`；未成功解析提交 SHA，因此它们是可变来源，不作为固定版本的验收证据。

## 结论与本项目建议

各项目没有统一的“Enter 必须拒绝”惯例。要区分文本框提交空字符串与选择列表确认高亮项。对本项目已有 `o / a / r` 文本输入，建议 **Enter 或纯空白直接返回 `r`**，并在提示中明确“Enter 默认拒绝”；`o / a` 才授权，其他非法文本保留三次重试。这是结合本项目交互形式的设计建议，不是声称业界一致如此。

用户已选择审批时 EOF、Ctrl+C 均表示拒绝当前操作。该语义可以保留，但 `prompt.py` 的异常捕获不足以保证整条执行链一致：还需与 REPL 的 SIGINT/Task 取消协调。本文不实现该接线，也不据此宣称真实终端取消已验证。

## Pi：选择列表的 Enter，不是文本空输入

- 官方 `permission-gate.ts` 是可加载的权限扩展示例：危险命令调用 `ctx.ui.select(..., ["Yes", "No"])`；未取得 `"Yes"` 时阻止调用，无 UI 也阻止。不能将该扩展示例概括为所有 Pi 会话默认都有审批弹窗。[扩展示例](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/examples/extensions/permission-gate.ts#L17-L27)
- `ExtensionSelectorComponent` 的初始索引为 0；Enter 确认当前高亮项。因此这个示例中直接 Enter 默认选中 Yes。这是有可见高亮项的选择列表，不是文本输入框中没有填写答案。[选择组件](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/modes/interactive/components/extension-selector.ts#L16-L21)、[Enter 与取消分支](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/modes/interactive/components/extension-selector.ts#L87-L102)
- 默认选择取消按键包含 Escape、Ctrl+C；选择取消返回 `undefined`，示例将其阻止。`confirm()` 本身也是 `Yes / No` 选择器，只有 Yes 返回 true，取消返回 false。[默认键位](https://github.com/earendil-works/pi/blob/main/packages/tui/src/keybindings.ts#L150-L154)、[选择与 confirm 接线](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/modes/interactive/interactive-mode.ts#L2536-L2577)

本次未核实原始终端 EOF 在 Pi TUI 中如何映射，不能直接套用 Python `input()` 的 `EOFError` 语义。

## mini-swe-agent：Enter 明确批准本批操作

`InteractiveAgent._ask_confirmation_or_interrupt` 明示 Enter 确认；经 `.strip()` 后的空字符串与 `/y` 都放行。其他评论拒绝本批并作为用户反馈，`/u` 拒绝并切到 human 模式。确认发生在执行 actions 之前，属于整批确认。[确认与批次执行](https://github.com/SWE-agent/mini-swe-agent/blob/main/src/minisweagent/agents/interactive.py#L115-L169)

`step()` 捕获 `KeyboardInterrupt` 后会再次询问用户评论或命令，再产生用户中断；因此不能把其 Ctrl+C 描述为直接返回一次普通拒绝。输入封装使用 `prompt_toolkit.PromptSession`；本次没有核实整个 CLI 对 EOF 的最终处理，也未做真实终端实验。[中断处理](https://github.com/SWE-agent/mini-swe-agent/blob/main/src/minisweagent/agents/interactive.py#L101-L114)、[输入封装](https://github.com/SWE-agent/mini-swe-agent/blob/main/src/minisweagent/agents/utils/prompt_user.py)

这个设计适合每批快速确认的工作流；本项目已有明确批准字母，可以保留不同的 Enter 默认值。

## OpenCode：官方文档只足以确认决定类型

官方权限文档给出 `once`、`always`、`reject` 三种结果，`always` 在本会话中批准匹配建议规则的后续请求。本次核查的文档未说明文本空输入、Enter 默认高亮或 Ctrl+C/EOF 的精确映射，故不据此推断。[What Ask Does](https://opencode.ai/docs/permissions/#what-ask-does)

## 本项目的最小调整与测试规格

1. 提示改为：`[o] 仅本次允许    [a] 本会话允许此类操作    [r/Enter] 拒绝`。
2. `raw.strip().lower()` 后，若为空立即返回 `r`；不消耗重试次数、不再次读取。
3. 其他非法文本仍提示并重试，三次非法后拒绝；现有 EOF/KeyboardInterrupt 返回 `r` 保留。
4. 回归覆盖空串、空格/Tab 均仅读取一次并拒绝；非法一次后可批准；三次非法拒绝；EOF/KeyboardInterrupt 拒绝；正常 o/a/r 与大小写、首尾空白兼容。
5. 输入函数单测只能验证返回值；审批时 Ctrl+C 是否会额外取消运行，须另测 REPL 与真实终端，不能用输入单测代替。

这是当前基础确认交互的局部调整，不增加 Bash 解析、授权缓存设计或新的里程碑要求。按仓库分工由用户实现核心，Codex 提供规格、测试与评审。
