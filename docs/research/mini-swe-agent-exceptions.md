# mini-swe-agent 的异常与中断设计

核查日期：2026-10-20。范围：官方仓库 `main` 的 DefaultAgent、InteractiveAgent、LocalEnvironment 和默认 LiteLLM 工具协议。为便于复查，以下源码链接固定到已核实的提交 [`04d809ceab9df28f9adaed044884180159172930`](https://github.com/SWE-agent/mini-swe-agent/commit/04d809ceab9df28f9adaed044884180159172930)。结论来自静态源码阅读，未运行该项目。

## 核心结论

值得借鉴的是：**把同一种决策放在一个边界处理**。工具执行失败在 Environment 转成输出，流程控制集中到 Agent.run，批量观察补齐集中到 formatter，交互中断集中到 InteractiveAgent.step。它的简洁也来自较弱的中断契约；不能从代码较短推导出它已覆盖本项目要求的异步取消和清理失败场景。

调用链本身也值得借鉴：DefaultAgent 直接持有 Model、Environment，`run → step → query/execute_actions → model/env`；InteractiveAgent 只在已有方法上覆盖交互行为，没有另建 runner、controller、coordinator 传递状态。处理统一不必增加对象：同一个 Agent 的少量方法已经形成清楚边界。[DefaultAgent](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/agents/default.py#L34-L146)、[InteractiveAgent](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/agents/interactive.py#L30-L130)

## 实际做法

| 边界 | 已核实行为 | 对应源码 |
| --- | --- | --- |
| 默认协议 | v2 默认使用原生 tool calls，只定义一个 bash 工具；允许一个响应含多个 action | [LiteLLM](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/models/litellm_model.py#L57-L97)、[actions_toolcall](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/models/utils/actions_toolcall.py#L9-L72) |
| Agent 循环 | 同步 query → 顺序 execute；run 统一接收流程控制异常；普通未捕获异常记录退出信息后重新抛出；finally 保存轨迹 | [DefaultAgent](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/agents/default.py#L67-L146) |
| 流程异常 | Submitted、LimitsExceeded、UserInterruption、FormatError 共用 InterruptAgentFlow，并携带消息；最后消息为 exit 时结束，否则下一轮 | [exceptions](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/exceptions.py)、[run](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/agents/default.py#L88-L116) |
| 普通执行失败 | execute 捕获 Exception，变成 returncode=-1、exception_info；非零命令退出也直接成为输出。因未抛出流程异常，默认可继续后续 action/模型轮次 | [LocalEnvironment.execute](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/environments/local.py#L21-L40) |
| 批量中断 | InteractiveAgent.execute_actions 在 finally 提交已收集 outputs；formatter 为缺失 outputs 补 action was not executed，与 tool_call_id 配对 | [interactive](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/agents/interactive.py#L115-L130)、[formatter](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/models/utils/actions_toolcall.py#L74-L108) |
| Ctrl+C | InteractiveAgent.step 捕获 KeyboardInterrupt，提示输入意见，再抛 UserInterruption；run 写入用户消息并继续。DefaultAgent 没有相同的专用捕获 | [interactive.step](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/agents/interactive.py#L101-L114) |

这里的“中断后继续”需要区分：InteractiveAgent 在收集用户意见后进入下一次模型查询；这与本项目“当前 run 返回、REPL 接收下一条用户输入”的接口形态不同。

## 清理保证的边界

LocalEnvironment 的 `_run` 用 Popen/communicate 同步执行。只在 TimeoutExpired 分支显式清理：POSIX 杀进程组，其他平台只调用 process.kill，然后 communicate 回收输出。[源码](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/environments/local.py#L66-L87)

在检查的执行链中，没有 KeyboardInterrupt 的子进程清理分支、asyncio.CancelledError 的传播、取消收尾期间重复中断保护，也没有“清理失败必须终止当前运行”的单独策略。若清理操作抛普通 Exception，会落入 execute 的普通错误输出路径。因此不能宣称其提供跨平台完整进程树清理或本项目要求的取消保证。[同文件 execute 与 _run](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/environments/local.py)

formatter 的补齐策略也不区分“命令尚未启动”和“命令已启动但结果未知”。InteractiveAgent 会保留已经 append 的输出；默认 DefaultAgent 使用列表推导，没有相同的 finally 部分结果提交。这些是具体实现边界，不能归纳为该项目所有路径都安全补齐历史。[DefaultAgent.execute_actions](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/agents/default.py#L143-L146)、[InteractiveAgent.execute_actions](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/agents/interactive.py#L115-L130)

## 版本区别

不要用早期“一轮一个正则提取的 bash 代码块”描述当前默认方案。官方 v2 迁移说明明确：默认改为原生工具调用，解析动作和格式化观察移到 Model；移除 ExecutionTimeoutError，将流程控制异常统一到 InterruptAgentFlow。[v2 迁移说明](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/docs/advanced/v2_migration.md#L148-L212)

## 对本项目的设计启发

以下是基于上述源码的设计推论，不是 mini-swe-agent 已实现的能力：

1. 普通工具失败用结果表示；取消和运行级故障保留控制流语义。不要让每层把所有异常重新分类。
2. 批量调用的历史补齐集中在一个收尾点，保留已完成结果；本项目需继续区分当前结果未知与剩余未执行。
3. 进程清理集中在拥有进程的执行层；是否继续会话集中在运行边界。清理失败的诊断与取消意图要同时保留。
4. 可以压缩重复分支和职责，但若保留异步流式、多工具及可继续会话的现有契约，就仍需明确处理这些状态；删除状态意味着改变承诺。
5. 优先在现有 `REPL → Agent.run → Registry → run_bash` 中缩短嵌套，用少量私有方法表达模型轮次、工具批次、待配对观察收尾；先避免新增运行器、协调器及相互转发的异常层次。正常完成和轮次耗尽可以是轻量返回值，取消保留原生控制语义。
