from __future__ import annotations
from typing import Any, Mapping, Protocol
from code_agent.safety.permission import Decision

class ApprovalPolicy(Protocol):
    """审批策略协议。每个需要审批的工具实现这三个方法"""
    def evaluate(self, args: Mapping[str, Any]) -> Decision:
        pass

    def scope_key(self, args: Mapping[str, Any]) -> str:
        """用户批准允许使用的操作，也就是ASK询问的时候，批准后不再问的命令"""
        pass

    def describe(self, args: Mapping[str, Any]) -> str:
        """展示给用户的描述文本"""
        pass