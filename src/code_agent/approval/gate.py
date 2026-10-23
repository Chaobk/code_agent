"""统一审批处理

用来统一控制工具的执行权限
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from code_agent.safety.permission import Decision

from .policy import ApprovalPolicy


class ApprovalGate:
    """统一审批入口。

    - 纯裁决交给 policy.evaluate
    - 需要确认时查 approved 缓存，未命中再走 prompt
    - 用户选 'a' 时把 (tool_name, scope_key) 写入缓存
    """

    def __init__(
        self,
        prompt_fn: Callable[[str], str] | None = None,
    ) -> None:
        self._prompt = prompt_fn
        self._approved: set[tuple[str, str]] = set()

    def check(
        self,
        tool_name: str,
        args: Mapping[str, Any],
        policy: ApprovalPolicy,
    ) -> tuple[bool, str]:
        """返回（是否放行）"""
        decision = policy.evaluate(args)

        if decision is Decision.ALLOW:
            return True, ""

        if decision is Decision.DENY:
            return False, f"命中硬拒绝规则：{policy.describe(args)}"

        scope = (tool_name, policy.scope_key(args))
        if scope in self._approved:
            return True, ""

        if self._prompt is None:
            return False, "当前入口不能进行交互确认"
        choice = self._prompt(policy.describe(args))

        if choice == "o":
            return True, ""

        if choice == "a":
            self._approved.add(scope)
            return True, ""

        return False, "用户拒绝"

    def reset(self) -> None:
        """清空会话级审批记忆。新会话或测试使用"""
        self._approved.clear()
