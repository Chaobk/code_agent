# 研究笔记导航

这里保存外部实现对照、原理实验和候选设计。项目的当前契约与实施顺序由 [PLAN.md](../../PLAN.md) 维护；已经采纳的接口见[架构](../design/architecture.md)和[运行流程](../design/run-flow.md)，验收证据见[计划历史](../plan-history.md)。

同一主题有总览、源码摘录和旧方案时，先读下面的主题入口，按问题追到专项材料。文件名中的日期表示调研时点，内容修订不表示重新运行过原实验，也不表示已验证上游最新版本。

## 当前 M5a：轨迹、回放与恢复

先看 [M5a 练习](../design/m5a-tracing.md) 的有效接口，再读研究：

| 问题 | 材料 | 阅读重点 |
|---|---|---|
| 为什么按已提交消息增量记录？ | [持久化时机](agent-persistence-timing-20261027.md) | 消息提交、执行步检查点与遥测导出的差别 |
| 一会话一文件是否合适？ | [会话持久化对照](coding-agents-persistence-20261027.md) | JSONL、数据库与客户端投影；有效前缀不等于可恢复检查点 |
| 会话是否等于一个 Agent 对象？ | [会话与运行实例](session-agent-binding.md) | 角色配置、历史归属与运行状态的生命周期 |

本项目先使用调用方持有的文本流，统一写记录并逐条 flush；默认存储接入、恢复和长期记忆分别推进。上述设计的写入失败、取消与工具执行顺序以练习规格为准。

## 模型流式与工具结果

| 问题 | 材料 | 阅读重点 |
|---|---|---|
| 网络分块、SSE、chunk 与 token 如何区别？ | [流式边界](streaming-sse-boundaries.md) | 外层事件 JSON 与内层 arguments 分片；教学解析器和应用 SDK 的范围 |
| 工具返回什么，如何送给模型？ | [工具结果](tool-results.md) | 领域数据、统一结果、消息关联和 UI 表示 |
| 核心怎样处理失败？ | [mini-swe-agent 异常](mini-swe-agent-exceptions.md) | 静态源码分析；不要把较短同步循环等同于本项目取消契约 |

## 运行结束与中断

先读[结束信号总览](agent-completion-signals.md)。下列文件保存来源细节，不能把不同日期和开发分支合并成同一版本：

- [Codex 结束路径](completion-signal-codex.md)：循环判定、协议事件及未核实范围。
- [OpenCode 结束路径](completion-signal-opencode.md)：step、attempt、drain 与本地续跑。
- [Pi 文档与 Roo 线索](completion-signal-firstparty.md)：Roo 资料的官方归属仍未确认。
- [其他 Agent 对照](completion-signal-others.md)：Cline、Aider、SWE-agent 与 Claude 的不同线路。
- [终端中断](agent-interruption.md)：按键、取消请求、清理与退出的区别。
- [运行生命周期](codex-opencode-run-lifecycle.md)：工具观察、受控失败与内部故障的传播。
- [运行流程研究](agent-run-flow-design.md)：重构前问题和候选方案；最终接口见设计目录。

## 权限、审批与进程清理

| 用途 | 优先阅读 | 补充材料 |
|---|---|---|
| 理解命令结构、规则、审批与隔离 | [Shell 权限调查](coding-agent-permissions-20261022.md) | [行业对比与静态实验](bash-safety-industry-20261022.md)、[命令权限详解](command-permission-policies.md) |
| 对照产品工具暴露和权限入口 | [OpenCode / Codex 对比](opencode-codex-tools-permissions.md) | [OpenCode V2 专项](opencode-tools-permissions-notes.md) |
| 核查 Pi 扩展的真实范围 | [Pi 权限原始来源核查](pi-permissions-primary-check-20261022.md) | [Pi 探索笔记](pi-agent-research-deepseek.md)保留问题与待核验线索 |
| 理解 Enter、EOF 与审批 Ctrl+C | [审批输入语义](approval-empty-input-20261026.md) | 本项目真实终端结果在计划历史 |
| 排查 Git Bash 取消后的管道等待 | [子进程清理对比](subprocess-cleanup-comparison.md) | [早期 psutil 选型](process-cleanup.md)、[实际修复](../design/m4b-git-bash-cancellation.md) |

基础审批已经贯通，规则与授权范围细化仍待完善；这些研究不把完整 AST、独立裁判或强沙箱重新变成进入 M5 的前置条件。

## 综合学习与模型探索

- [Pi 固定提交设计研究](pi-coding-agent-design.md)：覆盖循环、上下文、工具、扩展、协作与评测；按其中导航选读。
- [Hello-Agents 对照](hello-agents-learning.md)：区分教材原有实现与本项目学习安排。
- [面试资料核查](agent-interview-sources.md)：概念来源与项目表述边界；练习题见[面试问答](../interview-guide.md)。
- [Jev 本地部署资料](jev-local-deployment-2026-10-16.md)：区分官方服务、社区实现和版本化评测；尚无本机模型性能实验。

## 怎样理解证据

源码说明某提交中的路径，文档说明其公开契约，实验说明指定环境下实际观察；三者不相互替代。模型生成的探索稿和设计推论用于提出问题，不能单独证明产品行为。

引用实现时优先保存固定提交或标签。移动分支、第三方代理和代码搜索结果只保留各自范围；搜索未命中不能证明全仓不存在某能力。后续复核应记录具体改正的结论与来源，保留原实验日期，避免只更新标题日期便宣称全部验证。
