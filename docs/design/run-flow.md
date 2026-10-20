# 直接循环、明确结果与集中收尾

2026-10-20 实现。本次由用户明确授权 Codex 完成重构，供用户阅读源码；后续学习仍按 PLAN.md 分工。

## 推荐阅读顺序

1. [RunResult 与三类进度事件](../../src/code_agent/events.py)：先分清运行结果和工具结果。
2. [Agent.run](../../src/code_agent/core/agent.py)：从主循环读起，再读 `_request_turn` 和 `_settle_pending`。
3. [Registry](../../src/code_agent/tools/registry.py)：查找、校验、调用与包装，没有 handler 异常兜底。
4. [Bash](../../src/code_agent/tools/builtin/shell.py)：执行路径和统一资源收尾。
5. [REPL](../../src/code_agent/ui/repl.py) 与 [CLI](../../src/code_agent/ui/cli.py)：一次等待决定显示与继续。

## 数据与控制流

```text
CLI / REPL：采集输入
  → Agent.run：追加 user
    → _request_turn：读取流，组装并校验，实时发送 TextDelta
    → 提交完整 assistant
    → 逐个调用 Registry，记录 ToolResult，再通知 UI
    → 有调用则继续；无调用正常结束则返回 RunResult
    → finally：统一结清所有 pending
  → UI 显示运行结果、取消或致命错误
```

调用链仍为 `UI → Agent → Registry → 工具`，没有新增会话管理器、运行控制器或状态机框架。

| 表达 | 用途 | 上层动作 |
|---|---|---|
| `RunResult(completed, text=...)` | 正常结束，含原样最终正文 | UI 只结束正文行，不重放正文 |
| `RunResult(failed, error=...)` | 已知 SDK 或模型协议失败 | UI 显示一次原因，会话可继续 |
| `RunResult(max_steps, error=...)` | 请求轮数耗尽 | UI 显示一次原因，新运行重新计数 |
| `ToolResult` | 单次工具观察，包括参数错误、非零退出、超时 | 写入历史，交给下一轮模型 |
| `CancelledError` | 停止异步工作 | 收尾后重抛，REPL 只消费本次用户取消 |
| 其他异常 | 内部缺陷、清理或显示失败 | 收尾后向外传播，UI 诊断并结束会话 |

`RunResult` 是不可变 dataclass，字段为 `status/text/error`。没有旧字符串返回兼容路径。`AnyEvent` 只有 `TextDelta`、`ToolCallEvent`、`ToolResultEvent`；运行结束不再走 `RunFinished/ErrorEvent`。

## 核心为何只有一个历史收尾出口

`run` 主循环按执行顺序展开。`current_call_id` 在调用通知成功后、进入 Registry 前设置，结果写入历史后清空。

唯一的 `finally` 调用 `_settle_pending`：已记录结果不动；当前调用没有确定结果时补 `unknown`，其余补 `not_started`；`ToolExecutionCancelled` 已携带的确定观察原样保存。正常运行没有 pending 时收尾为空操作。

同一收尾路径覆盖工具取消、内部错误、历史提交和显示失败。补充观察只写历史，不向显示回调补发事件，避免在取消或显示器已坏时再次回调。历史校验仍是最后防线，不能为了继续运行而绕过它。

`_request_turn` 只在取得下一片和组装的位置捕获已知 SDK／协议异常；显示回调在捕获范围外。即使显示器抛出与 SDK 相同的异常类型，也仍作为显示故障传播。模型流使用 `aclosing`，显示失败或取消时也会关闭流。

## 工具与进程的职责

Registry 只转换未知工具和入参校验错误。handler 必须自行返回预期失败；Bash 的启动错误、`read_file` 的文件操作错误都在各自工具边界处理。handler 的编程错误和取消不再被包装成普通观察。

Bash 从创建子进程后即纳入资源保护：输出读取、身份查询和等待都在同一个 `try/finally` 内。所有退出调用 `_cleanup_execution`，依次尝试终止进程并回收读取任务。

输出收尾使用 2 秒独立上限；超时后取消并等待读取任务。进程清理失败也不会跳过输出任务回收。首个清理故障向外传播，其他收尾故障作为异常注释保存，原取消或内部错误保留在异常上下文中。

无法取得 psutil 身份时，只使用本次启动的子进程句柄终止和等待。后代清理仍基于快照，不承诺强隔离或副作用回滚。

## UI 的判断

REPL 只保留输入状态、当前 Task 和本次取消意图。正常返回后显示 `RunResult`；本地取消等待收尾后显示一次取消；外部取消向上传播；其他异常显示一次诊断并返回 1。没有 `finished_reason`、`run_started` 或渲染回写运行状态。

输入时 Ctrl+C 丢弃当前行，运行时只请求取消一次，收尾完成前不读取下一条输入。`/exit`、EOF 正常退出，退出时恢复原信号处理器。

`-p` 正常返回 0，失败／耗尽／致命错误返回 1，真实 Ctrl+C 由 CLI 外层映射为 130。流式正文与工具信息仍实时显示。

## 验证与边界

FakeLLM 与模拟工具验证返回值、协议提交、历史配对、失败后动作次数及显示去重；真实子进程测试验证管道和进程回收。真实 TTY 实验使用脚本模型及真实 Git Bash，发送实际 Ctrl+C；不把它描述为真实模型服务的取消验证。

证据位于 `artifacts/run-flow-refactor-20261020/`，摘要见 [计划历史](../plan-history.md)。本次不进入 M4c，也不自动确认 M4b 验收。
