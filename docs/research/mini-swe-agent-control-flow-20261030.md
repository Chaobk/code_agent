# mini-swe-agent：运行、收尾与保存流程

核查日期：2026-10-30。仅静态阅读官方 `main` 源码，未运行上游；未锁定提交，因此链接内容可能后续变化。

## 已核实的事实

- `DefaultAgent.run()` 循环调用 `step()`，区分格式错误、`InterruptAgentFlow` 控制流异常和其他异常。普通异常由 `handle_uncaught_exception()` 加入含异常信息的退出消息后重新抛出；每次循环的 `finally` 调用 `save()`，随后依据最后消息是否为 `exit` 决定结束。[官方 DefaultAgent 源码，L67–116](https://github.com/SWE-agent/mini-swe-agent/blob/main/src/minisweagent/agents/default.py#L67)
- `step()` 组合 `query()` 与 `execute_actions()`；`add_messages()` 只追加内存列表，不写文件。`serialize()` 汇总消息、状态和配置，`save()` 通过 `write_text(json.dumps(...))` 保存完整 JSON 快照，不是逐条追加 JSONL。[DefaultAgent，L63–66、L117–177](https://github.com/SWE-agent/mini-swe-agent/blob/main/src/minisweagent/agents/default.py#L117)
- `save()` 自身没有存储错误捕获，也没有临时文件替换。依据 Python 的异常执行顺序，循环 `finally` 中的保存错误会直接向外传播，且可能成为原运行异常之外的新异常；不能把该实现描述成完整的磁盘故障收尾方案。[DefaultAgent.save，L169–177](https://github.com/SWE-agent/mini-swe-agent/blob/main/src/minisweagent/agents/default.py#L169)
- `Submitted`、`LimitsExceeded`、`UserInterruption` 等属于 `InterruptAgentFlow`，携带待追加消息。它们表达流程转换，并非存储错误类型。[官方异常定义](https://github.com/SWE-agent/mini-swe-agent/blob/main/src/minisweagent/exceptions.py)
- `InteractiveAgent.execute_actions()` 集中保存已取得的 `outputs`，在 `finally` 中转成观察消息再追加；`step()` 则把 `KeyboardInterrupt` 转成交互中断流程。这是保留部分执行事实的参考，不能据此宣称已经覆盖本项目异步取消、unknown/not_started 和原生工具调用配对契约。[InteractiveAgent，L101–132](https://github.com/SWE-agent/mini-swe-agent/blob/main/src/minisweagent/agents/interactive.py#L101)
- 默认 CLI 负责装配模型、环境、Agent，随后调用 `agent.run()`；保存路径通过 Agent 配置传入，CLI 不在每个消息接点安排保存。[官方 mini 入口，L89–95](https://github.com/SWE-agent/mini-swe-agent/blob/main/src/minisweagent/run/mini.py#L89)

## 对 code_agent 的设计启发（本项目推导）

借鉴其清晰的 `run → step → query/execute` 分工，以及“先保留执行事实，再集中结束与保存”的组织方式。不要照搬每步末尾全量写 JSON：当前项目已选择 JSONL 增量保存，并要求工具调用记录保存成功后才执行工具，这些行为需要继续成立。

建议把消息校验与内存状态、增量保存进度和存储失败状态分清，由单一协调入口安排提交和保存；运行结束时复用同一个收尾入口，避免在不同层各加一个 `persist`、`io_failed` 或 `except OSError` 补丁。已取得的工具结果应先保存在运行状态中；存储失败后禁止后续副作用和重复写入，但内存收尾仍使用这些真实观察，而不是改成 unknown。

具体协调入口放在 History 还是由核心统一包装，需要按接口复杂度比较后决定。本调研不修改既有实现，不表示用户已批准重构；运行边界和只读回放也不因借鉴上游而自动完成。

## 后续落地（2026-10-30）

用户随后授权整理工具批次并将 History 收入 SessionManager。Agent 通过 Manager 提交消息和读取历史；History 仅维护内存校验与配对，Manager 统一协调内存提交、增量写入和保存失败状态。工具批次覆盖助手消息提交至结果结算，保存失败后禁止新任务与重复写入，内存收尾保留真实观察和原始取消或工具异常。完整回归 413 项通过、2 项因 Git Bash 环境跳过。当前接口以[会话管理规格](../design/session-management.md)为准；运行边界和只读回放仍待实施。
