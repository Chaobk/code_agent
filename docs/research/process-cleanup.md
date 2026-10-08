# Python 进程树清理方案

研究日期：2026-10-08。当前项目使用 Windows、Git Bash 和 asyncio。

| 方案 | 适用范围 |
| --- | --- |
| asyncio Process.kill | 直接进程的终止，需要另管子进程 |
| Windows taskkill /T /F | 系统命令提供树清理，但需处理外部命令失败 |
| psutil | 现成的进程查询、终止与等待 API，适合 M1 的尽力清理 |
| pywin32 + Job Object | 更强的 Windows 生命周期管理，需要在启动时建立进程归属 |

来源：[Python subprocess](https://docs.python.org/3.12/library/asyncio-subprocess.html)、[taskkill](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/taskkill)、[psutil 7.2](https://psutil.io/7.2/)、[Microsoft Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)。

## 当前选择

添加 psutil>=7.2,<8，并安装锁定的 7.2.2；进程树操作放进 asyncio.to_thread。启动时保存 psutil.Process，清理时复用，减少 PID 重新绑定的风险。后代先终止，再等待，存活者强制终止；根进程由 asyncio.wait 回收，避免 POSIX 双重等待。参考 [psutil 官方进程树示例](https://psutil.io/7.2/#kill-process-tree)。最后一项是依据双方回收机制的设计判断。

Windows 的 terminate 与 kill 等效，不宣称优雅关闭。进程已消失可忽略，权限失败和最终仍存活者明确报错；等待各阶段有期限。

## 实际范围

递归查询是快照，父进程或中间父提前退出、快照后新生的后代可能遗漏；这不是完整隔离或严格整组清理保证。更强的管理要在启动阶段采用进程组（POSIX）或 Job Object（Windows）。来源：[psutil children](https://psutil.io/7.2/#psutil.Process.children)、[Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)。

本步的八个清理测试通过，包括真实 Python 父子进程；未声称已通过 Git Bash 全任务验收。用户正在编写的 run_bash 返回封装、输出限额和完整超时/取消路径仍需后续验证。
