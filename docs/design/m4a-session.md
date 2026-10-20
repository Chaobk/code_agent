# M4a 实现练习：基础多轮会话

状态：用户已完成会话循环与 CLI 接线，相关自动化验证及真实会话外部复核通过，待用户确认阶段验收。阶段范围与验收以
[PLAN.md](../../PLAN.md) 为准；本文不表示 M3 或 M4a 已验收。

> 2026-10-20 接口更新：Agent.run 返回 RunResult，UI 在 await 结束后显示结束原因；事件仅承载进度。下文保留初始练习，当前契约以 [运行流程](run-flow.md) 为准。

## 先理解生命周期

读 `Agent.__init__`、`Agent.run` 和 `ConversationHistory`，解释三个问题：

1. 为什么一个会话只创建一次 Agent？历史、工具和执行环境分别在哪里保存？
2. 为什么第二次调用 run 能看到第一次的消息，而 max_steps 又重新计数？
3. 为什么 UI 应显示事件，却不再次打印 run 返回的正文？

可按 PLAN 的安排选读 Hello-Agents 第 7 章职责与第 9 章持续交互。

## 需要手写的三处

### 1. ui/repl.py：run_session

传入一个已有 Agent，循环读取输入并等待本次运行完成，然后再读下一条。

| 输入或结果 | 行为 |
|---|---|
| 空字符串、空白 | 继续读取，不调用模型 |
| 首尾去空白后为 `/exit` | 返回 0，不调用模型 |
| 输入函数抛出 EOFError | 返回 0，不调用模型 |
| 其他输入 | 将原文交给同一个 Agent.run |
| 正常运行结束 | 继续读取输入 |

`read_input` 是无参数同步函数，测试用它模拟输入；未传入时自行提供带提示符的终端输入。
默认输入应在调用时确定，避免把 input 固定在函数默认参数里。
这一版在等待用户输入时允许同步阻塞，不引入线程或额外输入库；运行中断在 M4b 设计。

创建使用传入 Console 的 `EventRenderer`，通过 `on_event=renderer.render_event` 显示事件；不要清空历史，
也不要把 run 的返回值再打印一次。本步不要求在异常后继续输入。

### 2. ui/cli.py：_run_interactive

调用现有核心工厂 `create_agent(settings)` 一次，再调用 `ui.repl.run_session`，
传入 Agent 和 CLI 已有的 console，返回会话退出码。建议使用模块导入
`from code_agent.ui import repl`，再调用 `repl.run_session(...)`。

创建时的 ValueError 沿用 `_run_once` 的诊断：显示一次“执行环境配置错误”并返回 1，
不进入会话。Agent、客户端和工具的组装继续由核心工厂完成。

### 3. ui/cli.py：ask

先选择运行方式，再加载配置：

| 参数与输入环境 | 行为 |
|---|---|
| prompt is None 且输入为终端 | 用一次 asyncio.run 启动 _run_interactive |
| prompt is None 且输入非终端 | 显示带 `-p` 的用法，返回 1，不加载配置或等待输入 |
| 明确提供空白 -p | 显示输入错误或用法，返回 1，不进入会话 |
| 非空白 -p | 沿用 _run_once，传入原始 prompt |

用已提供的 `_stdin_is_terminal()` 检查输入环境；`--version` 保持现有行为。
不要在每条用户输入上重新调用 asyncio.run 或创建 Agent。

## 开发与验证

练习顺序为先实现 run_session，再实现 _run_interactive，最后修改 ask；三处现已实现。
关键测试在 `tests/code_agent/ui/test_repl.py` 和 `test_cli_session.py`。
当前上述行为测试应全部通过。

```powershell
uv run pytest tests/code_agent/ui/test_repl.py tests/code_agent/ui/test_cli_session.py -q
uv run pytest tests/code_agent/ui -q
uv run pytest
uv run ruff check .
```

测试全部使用假模型或假运行，不需要密钥，不启动 Bash。
2026-10-19 UI、核心循环、历史与执行环境相关回归 185 项通过；Ruff 规则、改动文件格式和 diff 检查通过。
另用 FakeLLM 跑通完整 CLI 两次输入，仅创建一个 Agent，第二次模型请求包含此前历史，回复各显示一次，退出码为 0。

2026-10-19 在真实终端通过 `uv run codeagent` 完成独立靶子的修复和后续追问；共创建一个 Agent，
两次运行共 6 次模型请求，7 次工具调用与 7 个结果各配对一次。第二次请求保留第一次运行的完整有效历史，
无需工具即可复述上次的验收标记、修复和测试结果。空白不触发运行，`/exit` 正常退出，末尾回答标记只显示一次。
独立初始测试为 4 通过 / 1 失败，最终为 5 通过；仅 math_utils.py 有差异，其他固定材料哈希不变。
原始证据位于 `artifacts/m4a-session-20261019-run-001/`，摘要见[计划历史](../plan-history.md)。
真实运行结果仍待用户确认；失败、耗尽或 Ctrl+C 后续用属于 M4b，权限确认属于 M4c。
