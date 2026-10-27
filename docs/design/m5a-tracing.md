# M5a：会话轨迹与只读回放练习

## 目标与范围

**一个会话一份 JSONL，一个会话可包含多次运行。** 每次运行按历史提交顺序增量记录消息，并记录自己的开始和终态；回放只读，不调用模型或工具。进程异常退出后可查看已写入的完整记录，恢复会话属于 M5b。参考[持久化调研](../research/coding-agents-persistence-20261027.md)。

先提供 Python 调用接口和受控多轮实验，不要求 CLI 默认记录。核心由用户手写；文件写入与历史提交是两件事，UI 渲染事件继续只管显示。

默认接入会话存储时，记录统一放在 `~/.code_agent/sessions/`，与当前工作目录关联；会话需记录 ID、创建时间和规范化工作目录，以支持后续按项目查找。项目移动后旧路径不保证能识别新位置，恢复时需重新核实。会话头格式及路径配置在默认存储接入时确定；本练习先接受调用方提供的文件流，不自动创建默认目录。普通运行日志与会话记录分开管理。

## 接口与资源所有权

```python
# 接线示意：TraceWriter 和 trace_run 需要你完成实现。
with path.open("x", encoding="utf-8", newline="\n") as stream:
    writer = TraceWriter(stream)
    await trace_run(agent, "第一个任务", writer, run_id="1")
    await trace_run(agent, "第二个任务", writer, run_id="2")
```

- 由会话的调用方创建并持有文件。`x` 只创建新文件，已存在则报错、不会覆盖；父目录由调用方准备，打开失败时 Agent 不启动。`with` 在正常退出及普通异常传播时关闭文件，不需公开 `close()`。本步不向旧文件追加或实现恢复。
- `tracing.jsonl.TraceWriter(stream)` 接受已经打开的文本流；`write_record(record: dict)` 将一条对象写为 UTF-8 JSON、一行一个对象并以 `\n` 结尾，随后 `flush()`。不要求 `write_message()` 或 `write_end()`；开始、消息和终态共用同一入口。调用方保证单线程顺序使用。
- `core.trace.trace_run(agent, prompt, writer, *, run_id, on_event=None)` 记录一次运行，原样转发显示事件和返回值；同一个 writer 可顺序执行多次。调用方为每次运行提供本会话内唯一、非空的 `run_id`，不并发复用同一 Agent 或 writer。`trace_run` 不关闭 writer。
- 核心在 History 成功校验、提交及配对状态更新后调用可选的消息回调；传递独立快照，回调修改不能影响 History。本次 user、完整 assistant、tool 结果及取消收尾的补写观察都经过该路径，先前 system 和旧消息不重复写。回调仅在当前运行生效；未启用记录时原有入口行为不变。
- `replay(path)` 返回 `ReplayResult(records, complete, truncated_tail)`。`records` 为完整合法行，`complete` 表示至少有一次运行、每次开始的运行均有匹配终态且无残缺末行；它不表示会话已关闭或任务验收成功。`truncated_tail` 表示末尾有未以换行结束的数据。回放仅读取，不改写文件。

## JSONL 记录和顺序

```jsonl
{"version":1,"type":"run_start","run_id":"1"}
{"version":1,"type":"message","run_id":"1","message":{"role":"user","content":"你好"}}
{"version":1,"type":"run_end","run_id":"1","status":"completed","text":"你好","error":null}
{"version":1,"type":"run_start","run_id":"2"}
```

每次运行依次写入 `run_start`、本次新增消息、`run_end`，每行都单独 flush。`run_start` 写入成功后才调用 Agent；user 消息先提交和写入再请求模型；完整 assistant 消息先提交和写入，包含工具调用时写入成功后才执行工具；每个结果提交后立即写入，包括拒绝和取消结算。`TextDelta` 可以实时显示，未通过整轮校验时不能冒充正式历史消息。

终态 status 为 `completed`、`failed`、`max_steps`、`cancelled` 或 `exception`。受控返回照抄 `RunResult` 的 status、text、error；取消或其他异常时 text 为空、error 只写异常类名，异常仍向外传播。独立调用 ID、工具参数和结果原样保留。进程强杀不会产生 `run_end`；没有工具结果不表示工具未执行，不能自动重跑。

## 回放和失败边界

- 从第一行开始按顺序验证 JSON 对象、`version=1`、已知类型及字段；开始行的 `run_id` 须为非空字符串且本文件唯一。消息与结束行必须关联当前未结束的运行；不得同时开始两个运行，也不得写入无对应开始的消息或结束。结束行的 text 为字符串、error 为字符串或 null，status 属于上述集合。以换行结束的空行、非法 JSON、类型/版本不支持或关联错误抛 `ValueError`，提示行号。
- 末尾未以换行结束的数据视为残缺末行并舍弃，`truncated_tail=True`；前面的完整合法行仍可查看。空文件、只有已结束运行后接半行、或已开始但尚未结束的运行都返回 `complete=False`；空文件的 `truncated_tail=False`。文件读取失败原样传播。
- 消息提交后的写入或 flush 失败应显式传播，不能继续发起模型请求或执行后续工具，也不能写成功终态。已提交历史和已完成的工具观察不回滚；核心必须结清 pending，但禁用已失败的记录回调，避免清理反复失败。尤其是取消期间补写多个结果时，某次记录失败不能阻断其余历史结算；取消或原运行异常仍是主要异常，记录故障通过异常链暴露。运行异常同时伴随终态写入失败时采用相同规则；正常运行后的记录错误也要报告。
- 每条 `flush()` 将 Python 缓冲推给操作系统，改善应用进程被强杀时可查看的范围；不承诺断电安全。截断、损坏或文件系统错误仍可能发生，强杀实验不能证明 `fsync` 或恰好执行一次。

## 隐私和练习顺序

轨迹包含原始用户输入、工具参数和结果，可能含敏感内容；独立目录使用，不序列化 `Settings`、环境变量或凭据。忽略规则不等于访问控制。

1. **先实现 `TraceWriter(stream)` 和 `write_record(record)`。** 用调用方 `with` 创建新文件，写多条记录，确认每条是一行，`flush()` 后在文件仍打开时能读取，退出后文件关闭。
2. 实现 `replay`，覆盖同一文件两次运行、未结束前缀、残缺尾行与非法中间行。注意这不是恢复执行。
3. 在 `Agent` 提炼统一历史提交路径，接入可选回调；在 `core.trace` 实现 `trace_run` 的运行边界和异常处理。
4. 运行 `uv run pytest tests/code_agent/tracing -q`，再运行完整回归和 Ruff。模拟工具执行期间从同一文件读取，并在独立子进程强杀后查看有效前缀；展示正常、拒绝、取消、写入失败和强杀的证据，用户确认后进入 M5b。
