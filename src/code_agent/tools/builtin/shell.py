"""内置工具(D/F):shell 执行。

M1 实现 ``BashArgs`` 与异步 ``run_bash``,向模型注册为 ``bash``:
固定执行目录,返回 stdout / stderr / 退出码 / 超时与截断信息。
具体参数、输出预算与进程清理约定见 PLAN.md 的 M1 规格。
M3 起它的调用要先过 ``safety`` 的策略检查。
"""
import asyncio
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_OUTPUT_CHARS = 20_000


class BashArgs(BaseModel):
    """模型可指定的命令参数;执行目录和 Bash 路径由应用绑定。"""

    model_config = ConfigDict(extra="forbid")

    command: str = Field(min_length=1, description="在 Bash 中执行的命令")
    timeout_seconds: int = Field(default=30, ge=1, le=120, description="执行超时秒数")

    @field_validator("command")
    @classmethod
    def reject_blank_command(cls, value: str) -> str:
        """拒绝空白命令,保留原始命令中的缩进与换行。"""
        if not value.strip():
            raise ValueError("command 不能为空白")
        return value


async def run_bash(
        command: str,
        timeout_seconds: int = 30,
        *,
        workspace_dir: Path,
        bash_path: str,
) -> str:
    """运行一次独立 Bash 命令并返回 JSON 字符串(M1 待实现)。

    输出字段:stdout、stderr、exit_code、timed_out、truncated。
    固定执行目录为 workspace_dir;不跨调用保留 cd 或 export 状态。

    实现顺序:
    1. create_subprocess_exec 启动 Bash,传入 --noprofile --norc -c command;
       stdin 使用 DEVNULL,stdout/stderr 使用 PIPE。
    2. 并发读取两个输出管道,解码为文本并返回实际退出码。
    3. 加入超时、取消与进程树清理;输出缓冲限制为 MAX_OUTPUT_CHARS。
    4. 非零退出正常回传;超时标记 timed_out,exit_code 为 null。
    """
    process = await asyncio.create_subprocess_exec(
        bash_path, "--noprofile", "--norc", "-c", command,
        cwd=workspace_dir,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()

    print("stdout:", stdout.decode("utf-8", errors="replace"))
    print("stderr:", stderr.decode("utf-8", errors="replace"))
    print("exit_code:", process.returncode)


if __name__ == "__main__":
    asyncio.run(run_bash(
        "ls",
        workspace_dir=r"C:\Users\1004945427\Desktop\pros\code_agent",
        bash_path="bash"))
