"""既有循环测试的显式授权依赖；生产入口不使用这些放行策略。"""

from collections.abc import Mapping
from typing import Any

from code_agent.approval.gate import ApprovalGate
from code_agent.core.agent import Agent
from code_agent.safety.permission import Decision
from code_agent.tools.registry import ToolRegistry


class AllowPolicy:
    """仅让不以权限为目标的测试继续聚焦原有行为。"""

    def evaluate(self, args: Mapping[str, Any]) -> Decision:
        return Decision.ALLOW

    def scope_key(self, args: Mapping[str, Any]) -> str:
        raise AssertionError("ALLOW 不应查询授权缓存")

    def describe(self, args: Mapping[str, Any]) -> str:
        raise AssertionError("ALLOW 不应请求确认描述")


def make_agent(client: Any, registry: ToolRegistry | None = None, **kwargs: Any) -> Agent:
    """为已注册工具注入测试专用 ALLOW；未知工具仍由 Registry 拒绝。"""
    names = (
        [tool["function"]["name"] for tool in registry.model_tools()]
        if registry is not None
        else ["bash"]
    )
    return Agent(
        client,
        registry,
        approval_gate=ApprovalGate(),
        approval_policies={name: AllowPolicy() for name in names},
        **kwargs,
    )


async def allow_tool(name: str, args: dict[str, Any]) -> None:
    """Registry 分发测试显式提供放行回调。"""
