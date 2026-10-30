# 编程 Agent 如何判断 Shell 命令是否允许执行

调查日期：2026-10-22。只采用产品官方文档、官方开源仓库，以及单独标注的本地静态检查实验。

## 结论

这些产品没有一个能通过关键词表证明任意 Shell 程序安全。实际方案通常把几件事组合起来：

1. 解析命令结构，找到子命令、参数、重定向和已知包装器。
2. 使用可配置规则，以及部分产品内置的只读/危险操作启发式，决定允许、拒绝或审批。
3. 对需要审批的动作使用人工确认；部分产品、模式另有模型审查。
4. 另用操作系统或容器限制实际文件系统、网络权限。

“未命中危险规则”“无需弹确认”“在沙箱内运行”是不同结论，不能相互替代。脚本、测试和包管理命令仍可能执行项目中的任意代码；以下规则主要判断**调用是否符合权限策略**，并不完整证明程序的全部副作用。

## 证据范围与版本

| 对象 | 本次证据 | 限制 |
| --- | --- | --- |
| Codex | 当前官方文档；本地 `codex-cli 0.163.0-alpha.11` 的 `execpolicy check` | 静态检查器实验不等于完整 Agent 执行链 |
| Claude Code | 当前官方文档；2026-03-25 官方设计文章 | 未核验闭源分类器实现，也未实测产品 |
| OpenCode | 官方文档；官方仓库固定提交 `248bfbe1117da1e7cc23bd41273188318afec3a5` | 当前仓库已为 v2 目录布局；未安装运行 |
| Gemini CLI | 官方文档；官方仓库固定提交 `09d81bf116b6f0ef084731dc996aa9d56438bb70` | 默认分支快照不保证等于用户安装的稳定版；未安装运行 |

两个 SHA 均于本次通过 GitHub 官方 API 的 `commits/HEAD` 获取。源码结论是阅读所得，没有运行本文出现的删除、脚本或联网示例。文档可能与默认分支演进不同；发生区别时下面明确标注证据来源。

## 产品对比

| 产品 | 命令规则/结构分析 | 默认值与冲突处理 | 自动审查与隔离 |
| --- | --- | --- | --- |
| Codex | argv 前缀规则；受支持的字面线性 Shell 脚本可拆分检查 | 多条命中取 `forbidden > prompt > allow`；无匹配仍需上层策略决定 | 可选 auto-review 审批；文件系统/网络沙箱另行限制 |
| Claude Code | 命令文本模式、子命令及已知包装器；内置只读识别 | `deny > ask > allow`；常规权限模式与 auto 有不同处理 | auto 使用分类器审查部分动作；内置 Shell 沙箱有平台和工具范围限制 |
| OpenCode | 解析出命令资源字符串，再做通配符匹配 | 同一资源最后匹配规则胜出；产品多数权限默认 allow，求值器无匹配 fallback ask | `--auto` 自动批准非显式 deny 的请求；官方声明无内置安全隔离 |
| Gemini CLI | Shell AST、子命令、重定向；prefix/regex 策略和具体参数启发式 | 高 priority 规则胜出；默认交互 Shell 规则 ask，最终非交互 ask 转 deny；模式可覆盖 | 可配置 safety checkers；Conseca 模型检查默认关闭；沙箱另行配置 |

此表中的默认值都带有配置/模式条件，不能理解为所有调用始终遵循同一个决定。

## Codex：前缀规则、审批和沙箱分层

`prefix_rule` 匹配结构化 argv 的前缀；冲突取最严格决定。对 `&&`、`||`、`;`、`|` 连接的受支持字面线性脚本，Codex 可用 tree-sitter 拆开逐条检查。变量、赋值、重定向、替换、glob、控制流等复杂结构不会沿用这个拆分过程，而按完整 Shell invocation 匹配；**不能据此说复杂语法必然拒绝**。[Rules 官方文档](https://learn.chatgpt.com/docs/agent-configuration/rules)

auto-review 处理原本需要审批的动作；沙箱内已经允许的动作不会因此额外接受同样审查。独立 reviewer 结合压缩后的会话和精确动作，可做少量只读检查，给出决定及理由；超时与明确拒绝有区别。这个机制不等于放宽原有沙箱。[Auto-review 官方文档](https://learn.chatgpt.com/docs/sandboxing/auto-review)

文件系统和网络限制由执行环境落实，属于不同层次的控制。[Sandboxing 官方文档](https://learn.chatgpt.com/docs/sandboxing)

### 本地静态实验

使用临时规则 `git → allow`、`git reset --hard → forbidden`、`git clean → prompt`，只将下列字符串作为 argv 传给 `execpolicy check`，没有执行其中任何命令。

| 检查输入 | 检查器结果 |
| --- | --- |
| `git status` | allow |
| `git reset --hard` | forbidden |
| `git clean -fd` | prompt |
| `git -C . reset --hard` | allow，仅命中宽 `git` 规则 |
| `/usr/bin/git reset --hard` | 无匹配 |
| `python task.py` | 无匹配 |
| `echo "git reset --hard"` | 无匹配 |
| `bash -lc "git status && git reset --hard"` | 无匹配 |

这显示了**宽前缀授权与参数变体的局限**，不是推荐使用宽 `git` allow 规则。无匹配不等于最终放行；最后一例也不能证明实际 Agent 不拆 Shell，因为这里只调用规则检查器。

复现脚本为 `artifacts/bash-safety-research-20261022/check_rules.py`，原始结果为同目录 `results.json`，均为忽略的研究产物。8 例检查器退出码均为 0；环境出现 arg0 缓存权限警告，JSON 结果完整。首次默认 GBK 解码失败，指定 UTF-8 后重跑得到表中结果。

## Claude Code：命令模式加分类器，但模式不是安全证明

Bash 权限规则基于命令文本模式，复合操作逐子命令检查，并处理已知包装器；规则优先级是 deny、ask、allow。只读识别还需要检查参数与重定向，无法解析会转审批。官方明确提醒：`git push` 规则不覆盖 `git -C . push` 等所有等价写法，文本模式不能充当通用程序安全边界。[Permissions 官方文档](https://code.claude.com/docs/en/permissions)

auto mode 先处理权限规则及特例，常规本地路径中部分只读/工作区编辑直接放行，其余动作交给 classifier；服务器审查路径中只读或沙箱命令也可能等待审查。宽泛 Bash、解释器、包管理器 run 授权会在进入 auto 时清理。没有有效判决时拒绝执行。不要把这一模式概括为“每条命令都问模型”，也不要把某次历史文章的精确分类器输入当成所有版本通用事实。[Auto mode 决策过程](https://code.claude.com/docs/en/permission-modes#how-auto-mode-evaluates-actions)

2026-03-25 官方文章描述了快速初筛后再推理复核的两阶段分类器。这是该时间点的设计证据，不是本次准确率实测；其内部样本结果也不能直接推广为任意仓库的安全保证。[Auto mode 官方研究文章](https://www.anthropic.com/research/claude-code-auto-mode)

内置 Shell 沙箱通过 OS 文件和网络限制减少副作用，当前文档列出 macOS、Linux、WSL2 支持；原生 Windows 不具备同样内置沙箱，文件工具、MCP 和 hooks 也不处于该 Shell 沙箱范围内。[Sandboxing 官方文档](https://code.claude.com/docs/en/sandboxing)

## OpenCode：解析命令资源后通配符授权

当前文档将工具称为 `shell`，旧资料常称 `bash`。执行前先扫描命令，检查外部目录，再把每个命令的 `resource` 提交权限系统，`save` 则用于后续授权模式。[执行前 prepare](https://github.com/anomalyco/opencode/blob/248bfbe1117da1e7cc23bd41273188318afec3a5/packages/core/src/tool/plugin/shell.ts#L117-L145)

扫描有两条路径：legacy 使用 tree-sitter 遍历 `command` 节点，重定向时保留外层语句；portable 使用独立 scanner，遇到 `opaque` 返回工具错误。legacy 的无语法树同样报错，但已读代码没有在这里统一检查 `root.hasError`，因此不能写成“所有解析不确定都会审批”。路径选择受构建资源、channel 和实验配置影响。[扫描与失败分支](https://github.com/anomalyco/opencode/blob/248bfbe1117da1e7cc23bd41273188318afec3a5/packages/core/src/shell/parse.ts#L164-L225)、[路径配置](https://github.com/anomalyco/opencode/blob/248bfbe1117da1e7cc23bd41273188318afec3a5/packages/core/src/tool/plugin/shell.ts#L126-L129)

求值器对 action/resource 做通配符匹配，**最后匹配的规则胜出**，无匹配 fallback ask。一次调用含多个资源时，配置求值中的 deny 可阻断整次操作；否则有 ask 则等待。保存的批准另行合并，不能覆盖已检测到的配置 deny。[规则求值](https://github.com/anomalyco/opencode/blob/248bfbe1117da1e7cc23bd41273188318afec3a5/packages/core/src/permission.ts#L74-L83)、[多资源决定](https://github.com/anomalyco/opencode/blob/248bfbe1117da1e7cc23bd41273188318afec3a5/packages/core/src/permission.ts#L145-L165)

求值器 fallback 与产品默认配置不同：官方默认多数权限 allow，外部目录和重复调用保护 ask；用户可以配置更严格规则。`--auto` 自动批准原本会 ask、且未明确 deny 的请求，这不是模型风险分类。人工批准可为 once、always、reject。[Permissions 官方文档](https://opencode.ai/docs/permissions/)

官方安全声明直言 OpenCode 不提供 Agent 沙箱，permission 是保持用户知情的 UX 机制，不是隔离边界；真正隔离需放到容器或 VM。[固定提交 SECURITY.md](https://github.com/anomalyco/opencode/blob/248bfbe1117da1e7cc23bd41273188318afec3a5/SECURITY.md#L11-L19)

## Gemini CLI：策略优先级、具体参数启发式与可选检查器

策略支持 `commandPrefix`、`commandRegex` 等条件，高 priority 规则先匹配；不是“deny 在任何配置下天然优先”。产品预装的交互 Shell 规则为 ask，非交互对应规则为 deny；用户/管理员、模式及其他策略可覆盖。[Policy engine 文档](https://geminicli.com/docs/reference/policy-engine/)、[默认 Shell 规则](https://github.com/google-gemini/gemini-cli/blob/09d81bf116b6f0ef084731dc996aa9d56438bb70/packages/core/src/policy/policies/write.toml#L51-L55)、[非交互规则](https://github.com/google-gemini/gemini-cli/blob/09d81bf116b6f0ef084731dc996aa9d56438bb70/packages/core/src/policy/policies/write.toml#L96-L108)

当前源码使用 Bash tree-sitter，Windows 有 PowerShell AST 路径及特定 fallback。Shell 检查递归处理子命令和已知包装器，deny 会阻断，ask 会使整体等待；重定向可把 allow 下调为 ask，但有 `allowRedirection` 和模式例外。解析失败通常回到配置的 defaultDecision，缺省为交互 ask、非交互 deny；YOLO 有例外：参数受限制规则无法验证则 deny，无参数限制时可能 allow。[解析实现](https://github.com/google-gemini/gemini-cli/blob/09d81bf116b6f0ef084731dc996aa9d56438bb70/packages/core/src/utils/shell-utils.ts#L614-L638)、[失败处理与聚合](https://github.com/google-gemini/gemini-cli/blob/09d81bf116b6f0ef084731dc996aa9d56438bb70/packages/core/src/policy/policy-engine.ts#L447-L674)

它确实有人工编写的危险/已知安全识别，但比关键词更细。例如识别 `find`、`rg`、`git`、`sed` 时检查参数、可执行文件路径、工作区越界；检测危险通常转 ask，识别已知安全可以将部分 ask 转 allow。结果依赖 SandboxManager 实现、工作目录、信任状态和所匹配规则，不应概括为始终运行的全能判定器。[启发式接入](https://github.com/google-gemini/gemini-cli/blob/09d81bf116b6f0ef084731dc996aa9d56438bb70/packages/core/src/policy/policy-engine.ts#L365-L444)、[具体命令判断](https://github.com/google-gemini/gemini-cli/blob/09d81bf116b6f0ef084731dc996aa9d56438bb70/packages/core/src/sandbox/utils/commandSafety.ts#L218-L602)

Safety checker 是扩展机制，可能进一步 ask/deny；checker 异常按 deny 处理。**不能说默认每条 Shell 都调用 LLM**：Conseca 检查器在未启用时直接返回 allow，其设置默认 false；启用后才有根据上下文生成并执行安全策略的模型路径。[checker 调用](https://github.com/google-gemini/gemini-cli/blob/09d81bf116b6f0ef084731dc996aa9d56438bb70/packages/core/src/policy/policy-engine.ts#L959-L1012)、[Conseca 启用条件](https://github.com/google-gemini/gemini-cli/blob/09d81bf116b6f0ef084731dc996aa9d56438bb70/packages/core/src/safety/conseca/conseca.ts#L58-L105)、[默认设置](https://geminicli.com/docs/cli/settings/)

沙箱限制与策略引擎分开配置；当前官方文档同时讨论整个进程与工具级隔离，以及单次权限扩展。不要将“使用 Gemini CLI”直接等同于“已经隔离”。[Sandboxing 官方文档](https://geminicli.com/docs/cli/sandbox/)

## 对本项目的含义

以下是研究推论，不是调整当前计划或新增验收条件：

- 首先定义可支持的 Shell 语法、规则优先级、默认决定和错误行为，避免出现“识别失败视为安全”。
- 分开表达解析结果、权限决定和实际执行权限；审批允许某次调用，不等于证明脚本安全。
- 对少量已理解的命令检查 argv 与关键参数，可以减少误报；黑名单不应承担完整保护责任。
- M4c 基础策略和确认已接线；规则完善按案例后置，当前进入 M5a。模型审批和 OS 沙箱分别研究，不因本调研自动成为验收前置条件。
- 回归案例应覆盖复合命令、重定向、包装器、全局选项变体、未知脚本和解析失败，验证授权路径与进程是否实际启动。

本次只新增研究笔记与忽略的静态实验产物，没有修改代码、计划、依赖，也没有执行危险命令或完成端到端安全验证。
