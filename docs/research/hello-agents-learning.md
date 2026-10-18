# Hello-Agents 与 code_agent 学习路线对照

核查日期：2026-10-12。参考 Datawhale 官方仓库中的章节正文及社区贡献作者的实践记录。官方在线站本轮访问超时，下文使用可读取的 GitHub 正文链接；章节内容和框架版本可能继续更新。

本文件区分“教材内容”和“本项目建议”。建议服务于亲手实现编程 Agent 的学习目标，保留 [PLAN.md](../../PLAN.md) 的用户手写核心分工和逐阶段验收，不把整本教材变成实现清单。

本轮范围修订：用户明确把记忆系统、多 Agent 协作和通信纳入核心学习内容。因此下文撤销最初将这些能力仅列为可选阅读的建议，采用 M5c 最小记忆系统、M5d 知识检索及 M8a 本地协作、M8b MCP 工具接入、M8c A2A 任务通信主线；向量数据库、复杂远程部署和 ANP 完整实现仍可选。

2026-10-13 随项目计划将原 M8b 拆为 MCP、A2A 两个验收小步，并标明下述项目状态的历史范围；教材来源的核查日期仍为 2026-10-12。

## 1. 当前最值得吸收的内容

| 第一方材料 | 已核实的教材内容 | 对 code_agent 的建议 |
|---|---|---|
| [4.2.1 ReAct 的工作流程][react]、[4.2.3 编码实现][react-code] | 行动结果进入历史，供后续决策；示例用提示词约束并解析文本动作。 | 用自己的真实工具轨迹解释循环；保持已有原生 tool_calls，不退回文本正则解析，也不要求输出模型的私有思考。 |
| [7.4.5 FunctionCallAgent][function-call]、[7.5.1 工具基类与注册机制][registry] | 新版正文加入原生函数调用；工具系统分开规格描述、注册和执行。 | 对照 ToolSpec、ToolRegistry、ConversationHistory 的职责，重点理解 schema、参数校验、调用 ID 和观察消息怎样组成协议。无需换成 HelloAgents 依赖或照搬其类层次。 |
| [9.2.1 有效上下文][context-anatomy]、[9.2.2 智能体式搜索][jit] | 最小必要信息集、低重叠工具，以及按路径等轻量引用即时读取内容。 | 当前 Bash 按需读源码已经可以练习上下文工程：先定位，再读相关片段；按实际失败改提示词，避免预加载整个仓库。 |
| [12.1 评估基础][evaluation]、[12.2.1 BFCL 基准介绍][bfcl] | 评估应区分任务能力与工具调用能力，用统一样本比较改动；BFCL 检查工具选择和调用构造。 | 现在先借鉴评估思路：相同靶子、相同初始状态、检查测试与 diff。M7 再形成自动任务集，不立即接 BFCL 或大型外部榜单。 |

2026-10-12 调研时的项目记录：`core/agent.py` 已使用 `self.history`；取消分支保留工具携带的观察，否则为当前调用补 `execution=unknown`，其他 pending 调用补 `not_started`。用户确认采用正常 stop 且无调用即结束，finish_task 按需增强；当时待办是协议回归与非流式验收。此项来自项目代码与用户决定，非教材事实。M2a 随后已验收，当前进度以 [PLAN.md](../../PLAN.md) 为准，验收过程见 [计划历史](../plan-history.md)。

## 2. 按里程碑安排阅读与小实验

以下实验用于帮助理解既有验收目标，可与现有测试合并，不额外制造一套通关要求。

| 阶段 | 阅读重点 | 建议练习与要回答的问题 |
|---|---|---|
| M2a 协议复盘（已验收） | 第 4 章 ReAct；第 7 章原生调用和注册机制 | 画出一次“调用 → 执行 → 观察 → 下一次请求”；分别解释参数错误、取消及 stop 时有/无调用的状态变化。模型说完成、工具 completed、任务测试通过分别由什么证据决定？ |
| M2b 流式 | 复用第 7 章协议理解，结合当前模型 API 的 chunk 契约 | 给同一调用拆几个参数分片，说明执行前需要哪些信息；中途取消时证明未完成命令未执行。教材示例的文本流式不能代替工具分片协议。 |
| M3 / M4a–b 环境与会话 | [9.5.1 TerminalTool 设计][terminal]；第 8 章无状态模型说明 | 解释执行环境、会话、运行与请求的边界；在同一会话追问，再取消一次任务，观察历史和副作用状态。 |
| M4c 权限与确认 | [9.5.1 TerminalTool 设计][terminal]；本项目命令权限研究 | 用具体命令区分 cwd 绑定、权限规则、人工确认与真实隔离；教材“安全终端”表述不直接证明本项目沙箱能力。 |
| M5a / M5b 轨迹与恢复 | 第 8 章无状态与记忆问题；[9.4 NoteTool][notes] | 分清原始事件、可恢复历史与精选事实；恢复处理未知副作用，记录文件不能自动证明命令可重跑。 |
| M5c 最小记忆系统 | [8.1.3 记忆与 RAG 架构][memory-rag]、[8.2.1 记忆流程][memory-flow]、[8.2.5 记忆类型][memory-types] | 学习工作、情景、语义和程序性经验的区别；用文件完成写入、存储、检索、注入、更新/作废闭环，标记来源；跨会话召回与旧事实作废分别验证。程序性概念是本项目迁移选题，教材第四类实现实际为感知记忆。 |
| M5d 知识检索 | [8.1.3 记忆与 RAG 架构][memory-rag]；第 9 章上下文选择 | 对小型文档集做关键词定位、片段选择和带来源注入，比较不检索与检索的结果；先验证 RAG 闭环，再评估向量索引等增强。 |
| M6 裁剪与第二种模式 | [9.2.3 长时程上下文][long-context]、[9.3 ContextBuilder][builder]；[4.3 Plan-and-Solve][plan-solve] | 在确定性长历史上保留任务约束与工具配对；再在同一靶子比较 ReAct 和 plan-execute。先实现实际差异，再抽模式接口。教材的无工具数学规划示例需要改造成有执行结果反馈的编程任务。 |
| M7 评测 | 第 12 章评估基础与 BFCL；[9.6 代码库维护助手][maintainer]作为综合场景参考 | 将已验收的小任务积累为可复现任务集；看成功率、具体失败、步骤和实际可得的用量。每次恢复靶子初始状态，区分确定性协议测试与真实模型表现。 |
| M8a 本地多 Agent | [6.2.1 AutoGen 核心机制][autogen]、[6.2.2 软件开发团队][agent-team]、[6.4.1 CAMEL 自主协作][camel] | 在已有单 Agent 上练任务委派、独立历史、结构化消息/结果、结果汇总与错误/取消；从一个主 Agent 与两个只读子任务练习，不必先做大规模群聊或共享可变历史。 |
| M8b MCP 工具接入 | [10.2.1 MCP][mcp]、[10.2.2 MCP 客户端][mcp-client] | 辨析模型函数调用与外部工具接入；完成工具发现、定义转换、调用及结果映射的受控实验，并验证错误或断连。保留默认 Bash 的既有回归。 |
| M8c A2A 任务通信 | [10.3.1 A2A 动机][a2a-motivation]、[10.3.2 A2A 示例][a2a-example]、[10.4.1 ANP 目标][anp] | 辨析工具接入与 Agent 间通信；完成一个本机跨进程 A2A 小实验。项目内部消息契约不等于标准 A2A；若称实现标准，需核查当前官方规范及所用 SDK，教程模拟示例本身不能证明兼容。 |

第 9 章的 Gather → Select → Structure → Compress 可先当作检查输入上下文的四个问题。等裁剪案例出现后再决定需要哪些组件，不提前实现整个 ContextBuilder，也不复制第二套 TODO 状态。

## 3. 模式、记忆与扩展的边界

**模式。** [4.3][plan-solve] 将规划和逐步执行分开，[4.4 Reflection][reflection] 展示执行、反馈和修订。本项目先理解这些差异，M6 才实现第二种模式；Reflection 可选用测试失败或 diff 作为反馈练习，模型自评不能代替外部验证，不必新增第三种必做模式。

**记忆。** [8.1.3][memory-rag] 将交互记忆与外部知识检索分开；[8.2.1][memory-flow] 讲编码、存储、检索、整合和遗忘；[8.2.5][memory-types] 的实现类型是工作、情景、语义、感知。[9.4][notes] 用文本笔记记录长期项目状态。本项目 M5c 必须完成最小生命周期闭环，并理解程序性记忆如何表达可复用操作经验；这是用户扩大主线后的项目建议，不是教材已提供程序性实现的事实。M5d 必须完成带来源的知识检索实验。文件存储和关键词检索足以先验证机制，向量库、感知实现和复杂 RAG 后续按需深化；M6 再处理模型输入预算。

**协作与通信。** [6.2][autogen] 的团队案例展示角色分工、消息轮转和终止条件；[6.4.1][camel] 展示互补角色与协作约束。M8a 先实现本地委派与结果回传，独立历史和错误传播是项目设计建议，无需先采用这些框架。

**MCP / A2A / ANP。** [10.2.1][mcp] 区分 Host / Client / Server 及 tools / resources / prompts，[10.2.2][mcp-client] 演示工具发现与调用；[10.3.1][a2a-motivation] 讨论 Agent 任务、工件和通信，[10.4.1][anp] 讨论开放网络的服务发现、路由与身份。M8b 完成一项受控 MCP 工具接入，M8c 完成小型 A2A 跨进程实验，并辨析这些协议的边界，默认 Bash 无需改变；复杂远程部署及 ANP 完整实现可选。教材 [10.3.2][a2a-example] 明确采用模拟协议思想和部分 SDK 功能，不能把其自定义消息或本项目内部协议直接称为标准 A2A。

## 4. 社区 Code Agent 实践的可用经验

[Extra09 原作者实践记录][extra09] 描述了过早叠加多 Agent 和多层记忆、观察难以诊断、工具过碎导致选择负担等失败，以及从最小循环开始迭代的过程。它是社区经验，不是统一规范。

适合本项目采用的是：保留可核查的工具结果；遇到失败先找出是哪一层丢失证据；一次只比较一个设计改动。作者最终使用专用文件工具的决定，不推导为本项目也必须替换默认 Bash，工具增加仍由真实案例决定。

文中将普通 Bash 管道描述为把前一命令的 stderr 导向下一命令，这一解释不应照搬。普通 `|` 连接 stdout；学习重点应落在是否捕获 stderr，以及管道整体退出状态是否掩盖上游失败。本文采用其可诊断性经验，不把该解释作为协议或安全依据。管道行为以 [GNU Bash 手册](https://www.gnu.org/software/bash/manual/html_node/Pipelines) 为准。

## 5. 阅读优先级

现在精读第 4 章 ReAct、第 7 章原生函数调用和工具注册，第 9 章 9.2 的上下文原则；第 12 章先读评估思路。M5c / M6 细读第 8 章、第 9 章实现与规划范式；M8a 精读第 6 章协作机制，M8b / M8c 分别结合第 10 章 MCP、A2A 内容完成实验。第 5 章低代码平台、第 11 章训练、旅行助手和赛博小镇可作视野拓展，不设为编程 Agent 学习前置。

[react]: https://github.com/datawhalechina/hello-agents/blob/main/docs/chapter4/第四章%20智能体经典范式构建.md#421-react-的工作流程
[react-code]: https://github.com/datawhalechina/hello-agents/blob/main/docs/chapter4/第四章%20智能体经典范式构建.md#423-react-智能体的编码实现
[plan-solve]: https://github.com/datawhalechina/hello-agents/blob/main/docs/chapter4/第四章%20智能体经典范式构建.md#43-plan-and-solve
[reflection]: https://github.com/datawhalechina/hello-agents/blob/main/docs/chapter4/第四章%20智能体经典范式构建.md#44-reflection
[function-call]: https://github.com/datawhalechina/hello-agents/blob/main/docs/chapter7/第七章%20构建你的Agent框架.md#745-functioncallagent
[registry]: https://github.com/datawhalechina/hello-agents/blob/main/docs/chapter7/第七章%20构建你的Agent框架.md#751-工具基类与注册机制设计
[memory-rag]: https://github.com/datawhalechina/hello-agents/blob/main/docs/chapter8/第八章%20记忆与检索.md#813-记忆与rag系统架构设计
[memory-flow]: https://github.com/datawhalechina/hello-agents/blob/main/docs/chapter8/第八章%20记忆与检索.md#821-记忆系统的工作流程
[memory-types]: https://github.com/datawhalechina/hello-agents/blob/main/docs/chapter8/第八章%20记忆与检索.md#825-四种记忆类型
[autogen]: https://github.com/datawhalechina/hello-agents/blob/main/docs/chapter6/第六章%20框架开发实践.md#621-autogen-的核心机制
[agent-team]: https://github.com/datawhalechina/hello-agents/blob/main/docs/chapter6/第六章%20框架开发实践.md#622-软件开发团队
[camel]: https://github.com/datawhalechina/hello-agents/blob/main/docs/chapter6/第六章%20框架开发实践.md#641-camel-的自主协作
[context-anatomy]: https://github.com/datawhalechina/hello-agents/blob/main/docs/chapter9/第九章%20上下文工程.md#921-有效上下文的解剖学
[jit]: https://github.com/datawhalechina/hello-agents/blob/main/docs/chapter9/第九章%20上下文工程.md#922-上下文检索与智能体式搜索
[long-context]: https://github.com/datawhalechina/hello-agents/blob/main/docs/chapter9/第九章%20上下文工程.md#923-面向长时程任务的上下文工程
[builder]: https://github.com/datawhalechina/hello-agents/blob/main/docs/chapter9/第九章%20上下文工程.md#93-在-hello-agents-中的实践contextbuilder
[notes]: https://github.com/datawhalechina/hello-agents/blob/main/docs/chapter9/第九章%20上下文工程.md#94-notetool结构化笔记
[terminal]: https://github.com/datawhalechina/hello-agents/blob/main/docs/chapter9/第九章%20上下文工程.md#951-设计理念与安全机制
[maintainer]: https://github.com/datawhalechina/hello-agents/blob/main/docs/chapter9/第九章%20上下文工程.md#96-长程智能体实战代码库维护助手
[mcp]: https://github.com/datawhalechina/hello-agents/blob/main/docs/chapter10/第十章%20智能体通信协议.md#1021-mcp-协议概念介绍
[mcp-client]: https://github.com/datawhalechina/hello-agents/blob/main/docs/chapter10/第十章%20智能体通信协议.md#1022-使用-mcp-客户端
[a2a-motivation]: https://github.com/datawhalechina/hello-agents/blob/main/docs/chapter10/第十章%20智能体通信协议.md#1031-协议设计动机
[a2a-example]: https://github.com/datawhalechina/hello-agents/blob/main/docs/chapter10/第十章%20智能体通信协议.md#1032-使用-a2a-协议实战
[anp]: https://github.com/datawhalechina/hello-agents/blob/main/docs/chapter10/第十章%20智能体通信协议.md#1041-协议目标
[evaluation]: https://github.com/datawhalechina/hello-agents/blob/main/docs/chapter12/第十二章%20智能体性能评估.md#121-智能体评估基础
[bfcl]: https://github.com/datawhalechina/hello-agents/blob/main/docs/chapter12/第十二章%20智能体性能评估.md#1221-bfcl-基准介绍
[extra09]: https://github.com/datawhalechina/hello-agents/blob/main/Extra-Chapter/Extra09-Agent应用开发实践踩坑与经验分享.md
