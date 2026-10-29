# Python 进程树清理方案

研究日期：2026-10-08；项目状态修订：2026-10-28。本文保留早期 psutil 选择的理由；后续 Git Bash 故障与修复见[进程清理对比](subprocess-cleanup-comparison.md)和[修复记录](../design/m4b-git-bash-cancellation.md)。

| 方案 | 适用范围 |
| --- | --- |
| asyncio Process.kill | 直接进程的终止，需要另管子进程 |
| Windows taskkill /T /F | 系统命令提供树清理，但需处理外部命令失败 |
| psutil | 现成的进程查询、终止与等待 API，适合 M1 的尽力清理 |
| pywin32 + Job Object | 更强的 Windows 生命周期管理，需要在启动时建立进程归属 |

来源：[Python subprocess](https://docs.python.org/3.12/library/asyncio-subprocess.html)、[taskkill](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/taskkill)、[psutil 7.2](https://psutil.io/7.2/)、[Microsoft Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)。

## 早期选择与后续补充

当时添加 psutil>=7.2,<8 并锁定 7.2.2；当前依赖以 pyproject.toml/uv.lock 为准。进程树查询和等待放进 asyncio.to_thread。启动时保存 psutil.Process，清理时复用，减少 PID 重新绑定的风险。后代先终止，再等待，存活者强制终止；直接子进程通过 `await process.wait()` 回收，避免 POSIX 双重等待。参考 [psutil 官方进程树示例](https://psutil.io/7.2/#kill-process-tree)。最后一项是依据双方回收机制的设计判断。

Windows 的 terminate 与 kill 等效，不宣称优雅关闭。进程已消失可忽略，权限失败和最终仍存活者明确报错；等待各阶段有期限。

## 实际范围

递归查询是快照，父进程或中间父提前退出、快照后新生的后代可能遗漏；这不是完整隔离或严格整组清理保证。更强的管理要在启动阶段采用进程组（POSIX）或 Job Object（Windows）。来源：[psutil children](https://psutil.io/7.2/#psutil.Process.children)、[Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)。

早期八个清理测试通过，包括真实 Python 父子进程；这是当时证据，不能证明 Git Bash 后代均可被快照发现。后续故障表明 MSYS sleep 可能遗漏，当前 Windows CLI 在共享控制台中创建进程组并先发送定向 CTRL_BREAK，再做快照回收和有界输出收尾。该机制仍不承诺强隔离；测试与真实终端证据见[计划历史](../plan-history.md)。
