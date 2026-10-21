# M4b：Git Bash 取消故障与修复

2026-10-21。用户授权 Codex 修复，并要求参考成熟实现、精简方案。
当前使用 Windows 控制台进程组，已删除早先的 MSYS 进程表解析方案。
本次修复不替代用户的 M4b 验收确认，不进入 M4c。

## 已复现的原因

用户命令为 `sleep 20 && echo "done"`。不调用模型，只取消 `run_bash` 的 Task，
同样出现 `RuntimeError: 等待 Bash 输出读取收尾超时`。

当前 `psutil.Process.children(recursive=True)` 使用 Windows 父子关系，
本次 Git Bash 的 MSYS 子进程关系不能由该快照完整还原。诊断中：

| 对象 | Windows PID | Windows 查询 / MSYS 查询 |
|---|---|---|
| Bash 启动器 | 15836 | Windows 原生根进程 |
| 内部 Bash | 19188 | Windows 可发现；MSYS PID=411 |
| sleep | 2020 | Windows 快照遗漏；MSYS PID=412、PPID=411 |

终止两个 Bash 后，遗漏的 sleep 仍持有 stdout/stderr 的写端，读取端等不到 EOF，
2 秒后抛出清理故障。事件循环关闭时的 transport 析构错误是未回收管道的后续表现。
独立脚本在终止 Bash 前补齐 MSYS 后代，原命令即可正常传播 `CancelledError`，
三个已关联进程均退出。随后以控制台进程组替代这套专用解析，验证如下。

## 精简修复

主要改动在 `src/code_agent/tools/builtin/shell.py`：

1. Windows 启动 Bash 时指定 `CREATE_NEW_PROCESS_GROUP`，为本次命令建立独立组。
2. 取消或超时进入统一收尾时，对该组发送 `CTRL_BREAK_EVENT`，给 MSYS 100ms
   传播控制事件，再执行既有原生进程回收和输出任务收尾。
3. 只有本次明确创建的新组才发送该信号；发送失败也尝试原生资源回收，随后传播故障。
   保留 2 秒输出收尾上限、历史补齐和取消传播。

这是 Windows 提供的控制台机制，要求调用方和组成员共享同一控制台。
无控制台、另建控制台或主动忽略控制事件的程序，不承诺整组强制终止；
若要覆盖这些场景，应单独验证 Job Object 等执行后端，不能把本修复称作沙箱。

## 上游对照和取舍

详见 [子进程清理方案对比](../research/subprocess-cleanup-comparison.md)。

- mini-swe-agent：POSIX 使用新 session 和 killpg，Windows 只杀直接进程。
- OpenCode：Windows 使用 taskkill /T。本机对原命令实测仍遗漏 sleep，不能直接替换。
- Codex：Windows pipe 使用 Job Object；管理范围更强，但 Python 接入比当前控制台
  方案更重。这里采用已实测可行的标准库控制台机制。

## 验证

`tests/code_agent/tools/builtin/test_shell_git_bash.py` 覆盖真实 Git Bash 的取消和超时，
检查及时结束、进程退出和两根管道 EOF，不靠等 sleep 自然结束通过。
`test_shell.py` 还覆盖控制事件发送失败时仍回收原生进程与输出任务。
早先三个 MSYS 解析测试随废弃方案删除，真实副作用回归保留。

真实 TTY 使用脚本模型、真实 CLI/REPL 和 Git Bash：输入 sleep → 实际 Ctrl+C →
重新提示 → 输入 after 执行下一条 Bash 命令 → /exit。应用退出码 0，pending 为空，
两次调用的 stdout/stderr 均 EOF，记录的原生进程全部退出，终端未出现清理超时或
transport 析构异常。PowerShell PTY 宿主在接收 Ctrl+C 后返回 1，与应用捕获的退出码
分别记录，不混为一谈。未访问真实模型服务。

原始材料在已忽略的 `artifacts/bash-cancel-20261021/`；完整回归摘要见计划历史。

```powershell
uv run pytest tests/code_agent/tools/builtin/test_shell_git_bash.py -q
uv run pytest
uv run ruff check .
```

真实信号和脚本 Task.cancel 的验证分别记录，不用后者替代前者。
