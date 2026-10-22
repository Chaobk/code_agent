"""终端审批交互"""

from __future__ import annotations

import sys

_VALID_CHOICES = {"o", "a", "r"}
_MAX_RETRIES = 3

def prompt_decision(description: str) -> str:
    """向用户询问审批决策。

    返回：
        'o' 仅本次允许
        'a' 本会话允许此类操作
        'r' 拒绝（包括非交互环境、EOF、Ctrl+C、非法输入超限）
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
        "[o] 仅本次允许    [a] 本会话允许此类操作    [r] 拒绝",
        file=sys.stderr,
    )

    for _ in range(_MAX_RETRIES):
        try:
            raw = input("> ")
        except (EOFError, KeyboardInterrupt):
            print("", file=sys.stderr)
            return "r"

        choice = raw.strip().lower()
        if choice in _VALID_CHOICES:
            return choice

        print("无效输入，请输入 o / a / r。", file=sys.stderr)

    print("多次无效输入，视为拒绝。", file=sys.stderr)
    return "r"