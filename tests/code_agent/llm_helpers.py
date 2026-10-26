"""使用 SDK 的真实响应类型构造精简测试分片。"""

from typing import Any

from openai.types.chat import ChatCompletion, ChatCompletionChunk


def make_completion(**kwargs: Any) -> ChatCompletion:
    """补齐与测试行为无关的响应元数据。"""
    choices = kwargs.pop("choices", [{"message": {"role": "assistant", "content": ""}}])
    return ChatCompletion.model_validate(
        {
            "id": "test-completion",
            "created": 0,
            "model": "test-model",
            "object": "chat.completion",
            **kwargs,
            "choices": [
                {"index": index, "finish_reason": "stop", **choice}
                for index, choice in enumerate(choices)
            ],
        }
    )


def make_chunk(**kwargs: Any) -> ChatCompletionChunk:
    """补齐分片元数据，保留调用者给出的空 choices 和原始 delta。"""
    choices = kwargs.pop("choices", [])
    return ChatCompletionChunk.model_validate(
        {
            "id": "test-completion",
            "created": 0,
            "model": "test-model",
            "object": "chat.completion.chunk",
            **kwargs,
            "choices": [{"index": index, **choice} for index, choice in enumerate(choices)],
        }
    )
