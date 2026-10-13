# Agent 开发面试题资料核查

核查日期：2026-10-13。本文给 [面试问答](../interview-guide.md) 提供第一方依据，不统计公司真题或题目出现频率。官方概念、工程建议和本项目已实现能力分别表述；动态文档在实施时需再次核对。

项目状态以 [PLAN.md](../../PLAN.md) 为准：M0、M1、M2a 已验收；M2b 进行中，CLI 尚未接入流式 Agent；M3–M8 待实施。下述来源只用于理解和设计，不要求引入对应框架、云服务或 SDK。

## 1. Workflow、Agent 与手写循环

- **来源：**[Anthropic：Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)，尤其 What are agents、When to use agents、Agents。
- **核验结论：**该文用预定义代码路径与模型动态决定流程区分 workflow 和 agent；Agent 通过环境反馈循环行动，通常需要停止条件。复杂度伴随延迟、成本和错误累积，应以任务收益决定。
- **常见误答：**“调用一次大模型就是 Agent”“使用框架才能算 Agent”“ReAct 必须输出可见的完整思维链”。本项目应通过工具选择、观察回灌与下一步行动解释闭环，不依赖展示私有推理。
- **项目落点：**M2a 可讲循环、结束协议和最大轮数；plan-execute 属于 M6 设计方案。该文是架构参考，不是所有 Agent 的唯一分类标准。

## 2. Tool calling、schema 与执行权限

- **来源：**[Anthropic：Tool use overview](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview)；安全边界另见第 8 节 MCP 规范。
- **核验结论：**客户端工具由模型返回调用请求，应用实际执行并回传结果。输入 schema 描述结构；它不替应用判断操作是否获授权、是否符合业务意图或是否已成功产生预期结果。这一安全结论是模型/执行职责分离与 MCP 安全要求的工程推论。
- **常见误答：**“JSON/schema 合法就可以执行”“模型直接调用 Python 函数”“提供 strict schema 就解决权限问题”。还需参数与业务校验、授权策略、结果观察和外部验收。
- **项目落点：**M2a 已有 Registry 校验及调用 ID 配对；M3/M4 的策略和确认待实施。来源中的 Claude 消息形状不等同于本项目 Chat Completions 协议，不能照搬字段。

## 3. asyncio、取消与进程清理

- **来源：**Python 3.12 官方文档：[Tasks / cancellation / to_thread](https://docs.python.org/3.12/library/asyncio-task.html)、[Subprocesses](https://docs.python.org/3.12/library/asyncio-subprocess.html)、[Future.cancel](https://docs.python.org/3.12/library/concurrent.futures.html#concurrent.futures.Future.cancel)。
- **核验结论：**取消在协程下一次有机会时抛出 `CancelledError`，通常应清理后继续传播；`to_thread` 把阻塞函数放到线程中，已经运行的线程工作不能靠 Future 取消强行停止。取消等待也不能代替对子进程的显式终止和回收。
- **输出边界：**只等待子进程退出而不消费 PIPE 可能死锁；`communicate()` 会读取两路输出，但将结果缓存在内存。面向大输出的分块读取、保留预算与继续排空，是本项目的工程选择。
- **常见误答：**“async def 内调用同步函数不会阻塞”“超时等于撤销副作用”“杀父进程保证所有后代消失”。
- **项目落点：**M1/M2a 可讲 Bash 清理和取消配对；后代快照只提供尽力清理，严格进程隔离是扩展，不是既有能力。

## 4. RAG 与微调

- **来源：**[Lewis 等：Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks](https://arxiv.org/abs/2005.11401)，原论文摘要及方法。
- **核验结论：**论文把模型参数中的知识与可检索的外部非参数记忆结合，而且训练了检索增强模型。因此 RAG 和微调可以组合，不能将它们描述为互斥技术。
- **工程推论：**需要更新外部知识、定位依据时先考虑检索；需要调整任务行为或输出风格且有训练数据时可评估微调。两者都不能保证事实正确或消除幻觉，应按目标与评测选择。
- **常见误答：**“RAG 就是向量数据库”“RAG 永远不需要训练”“微调能取代所有知识更新”。原论文使用稠密索引不意味着所有检索增强应用必须使用向量库。
- **项目落点：**M5d 计划先用文件/关键词检索完成来源可核查的闭环；当前不能声称已实现 RAG、向量库或微调。

## 5. 检索评测与回答评测

- **来源：**[Microsoft：RAG evaluators](https://learn.microsoft.com/en-us/azure/foundry/concepts/evaluation-evaluators/rag-evaluators?view=foundry-classic)。
- **核验结论：**检索过程质量与生成结果质量应拆开评估；带相关性标签可评价排序（如 NDCG），回答还要检查 groundedness、相关性与完整性。没有标签的模型评分与有标注的检索指标不是同一种证据。
- **常见误答：**“召回了文档就说明回答正确”“引用存在就表示来源支持结论”“答案正确就证明检索没有问题”。
- **项目落点：**M5d 可用人工标注的小问题集检查是否找到相关片段、引用是否支持结论，再比较最终回答；M7 再自动化。指标与样本数量不增加当前里程碑门槛。

## 6. 历史、记忆与恢复

- **来源：**[LangGraph：Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)。
- **核验结论：**该框架区分线程内状态检查点与跨线程长期存储；内存型 saver 不代表进程重启后持久化。持久化状态与筛选可复用知识是不同职责。
- **常见误答：**“聊天记录落盘就是长期记忆”“恢复状态能恢复磁盘和撤销外部 API”“有 checkpoint 就能安全重试所有节点”。后两项是必须另外证明的副作用保证，不能从状态保存推出。
- **项目落点：**当前只有 History；M5 分别设计只读轨迹回放、已配对检查点恢复、记忆生命周期、知识检索。无需迁移到 LangGraph，也不能把计划中的恢复说成已实现。

## 7. 单 Agent 与多 Agent 的取舍

- **来源：**[Anthropic：How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)，Benefits、Architecture、Evaluation。
- **核验结论：**可独立探索的任务能利用分工和独立上下文；多 Agent 也增加用量、协调和可靠性问题。来源中的研究任务成绩、成本倍数仅属于其特定系统，不应推广成本项目或所有编码任务的收益。
- **常见误答：**“多几个 Agent 一定更准确、更便宜”“多数投票就能保证真相”“子 Agent 的结论可以直接拼接”。
- **项目落点：**M8a 待实施：独立子会话、证据交接、取消传播和冲突核实；使用相同任务与资源条件对照单 Agent。当前尚无多 Agent 实测收益。

## 8. MCP 与 A2A

- **来源：**[MCP 2025-11-25 规范](https://modelcontextprotocol.io/specification/2025-11-25)；[A2A 规范](https://a2a-protocol.org/latest/specification/)，尤其核心概念、Versioning 与 Appendix B。
- **核验结论：**MCP 定义 Host / Client / Server，用于连接工具和上下文等能力；A2A 面向独立 Agent 的能力发现、消息、任务状态和产物交换，两者可组合。MCP 不只是模型的 tool_calls 字段，A2A 也不等于多个本地循环。
- **安全与版本：**MCP 规范明确协议本身不能强制实现全部安全原则。这里引用固定的 2025-11-25 版本，不宣称它是最新版本；A2A 的动态页面本次包含 1.0 与 0.3 的兼容说明。实施时固定规范、SDK 和传输版本，验证实际能力，不混抄不同版本例子。
- **常见误答：**“接入协议自动拥有沙箱和业务权限”“交换几条 JSON 就算标准兼容”“MCP 与 A2A 必须二选一”。
- **项目落点：**M8b/M8c 分别待实施；本机实验也需检验发现、调用或任务、失败与取消；远程认证和生产部署是扩展。

## 9. Agent 评测与稳定性

- **来源：**[Anthropic：Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)，Coding agents、Non-determinism、Roadmap。
- **核验结论：**明确任务与稳定环境是基础；编码任务可用失败测试转绿与既有测试不退化来判定，并检查过程和产物。结果评测不要求每次采用同一行动路径；模型裁判需明确标准并与人工判断校准。
- **常见误答：**“Agent 说完成等于成功”“工具正常退出等于任务完成”“一次通过代表稳定”“pass@k 是连续 k 次全成功”。`pass@k` 关注 k 次尝试至少一次成功，`pass^k` 关注全部成功，口述时不能混淆。
- **项目落点：**已有 FakeLLM、工具测试与独立靶子验收可讲；M7 自动任务集和重复运行统计待实施。当前以成功次数/总次数诚实描述，不编造准确率、生产用户或性能提升。
