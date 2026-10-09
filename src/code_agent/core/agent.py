"""内核:Agent 编排器。

M1 实现。这里是**唯一**把各能力串起来的地方:

    用户输入 ─► modes(决定下一轮请求)─► llm(调用模型)
                    ▲                        │
                    │                        ▼
              context(历史) ◄── tools(执行工具) ◄── events(推给界面)

各能力子包互不 import,只共享 ``code_agent.events`` 的数据模型;
所有跨能力的连接都集中在本模块,这样替换任一能力都不需要动别的包。
"""

import asyncio
import inspect

from pydantic import ValidationError

from code_agent.config import load_settings
from code_agent.events import Role, ToolResult
from code_agent.llm.client import LLMClient
from code_agent.tools.builtin import create_builtin_registry

registry = create_builtin_registry()

SYSTEM_PROMPT = """
    你是一个编码助手，要尽力完成用户询问的问题

    你现在可以使用的工具有:
    {tools}

    当以认为你已经处理完这次的任务，无需多余的轮次进行处理的时候，回答 "END_TASK"
"""


class Agent:
    def __init__(self, client: LLMClient, registry: dict, max_steps: int = 10):
        self.client = client
        self.registry = registry
        self.max_steps = max_steps
        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": spec["description"],
                    "parameters": spec["args_model"].model_json_schema(),
                },
            }
            for name, spec in self.registry.items()
        ]
        tool_description = "\n".join(
            f"- {name}: {spec['description']}" for name, spec in self.registry.items()
        )

        system_prompt = SYSTEM_PROMPT.format(tools=tool_description)
        self.messages = [{"role": "system", "content": system_prompt}]

    async def run(self, prompt: str) -> str:
        self.messages.append({"role": "user", "content": prompt})
        for _ in range(self.max_steps):
            response = await self.client.complete_turn(self.messages, tools=self.tools)
            choice = response.choices[0]

            if choice.finish_reason not in ("stop", "tool_calls"):
                raise RuntimeError(f"模型未正常结束：{choice.finish_reason}")

            message = choice.message

            self.messages.append(message.model_dump())

            if message.tool_calls:
                for call in message.tool_calls:
                    output = await self._execute_tool(call.function.name, call.function.arguments)
                    self.messages.append(
                        {"role": Role.TOOL.value, "tool_call_id": call.id, "content": output}
                    )
                continue

            if choice.finish_reason == "stop":
                return message.content or ""

            raise RuntimeError("模型报告工具调用，但没有返回调用信息")

        raise RuntimeError(f"达到最大请求轮数：{self.max_steps}")

    async def _execute_tool(self, name: str, arguments: str) -> str:
        spec = self.registry.get(name)
        if spec is None:
            return f"工具错误：未注册的工具{name}"

        try:
            # 校验一下模型给的参数能不能用
            args = spec["args_model"].model_validate_json(arguments)

            handler = spec["handler"]
            kwargs = args.model_dump()

            if inspect.iscoroutinefunction(handler):
                result = await handler(**kwargs)
            else:
                result = await asyncio.to_thread(handler, **kwargs)

            if isinstance(result, ToolResult):
                return result.model_dump_json()

            return result

        except ValidationError as exc:
            return f"工具参数错误： {exc}"
        except Exception as exc:
            return f"工具执行错误：{type(exc).__name__}:{exc}"


if __name__ == "__main__":
    client = LLMClient(load_settings())
    agent = Agent(client, registry, 10)
    asyncio.run(agent.run(r"帮我在当前目录写一个markdown文件，内容就是agent的学习路径"))
    print(agent.messages)
