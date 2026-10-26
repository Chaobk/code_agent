"""终端审批交互"""

from __future__ import annotations

import signal
import sys

_VALID_CHOICES = {"o", "a", "r"}
_MAX_RETRIES = 3


def prompt_decision(description: str) -> str:
    """向用户询问审批决策。

    返回：
        'o' 仅本次允许
        'a' 本会话允许此类操作
        'r' 拒绝（包括空白输入、非交互环境、EOF、Ctrl+C、非法输入超限）
    """
    if not sys.stdin.isatty():
        print(
            "[approval] 非交互环境，自动拒绝本次操作。",
            file=sys.stderr,
        )
        return "r"

    print("", file=sys.stderr)
    print(description, file=sys.stderr)
    print(
        "[o] 仅本次允许    [a] 本会话允许此类操作    [r/Enter] 拒绝",
        file=sys.stderr,
    )

    for _ in range(_MAX_RETRIES):
        # 审批期间，如果接收到 Ctrl+C 信号，不直接退出
        previous_handle = signal.signal(signal.SIGINT, signal.default_int_handler)
        try:
            raw = input("> ")
        except (EOFError, KeyboardInterrupt):
            print("", file=sys.stderr)
            return "r"
        finally:
            signal.signal(signal.SIGINT, previous_handle)

        choice = raw.strip().lower()

        if not choice:
            return "r"

        if choice in _VALID_CHOICES:
            return choice

        print("无效输入，请输入 o / a / r。", file=sys.stderr)

    print("多次无效输入，视为拒绝。", file=sys.stderr)
    return "r"
