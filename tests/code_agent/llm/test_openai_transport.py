"""使用真实 SDK 与内存 HTTP 传输验证迁移边界，不访问外部服务。"""

import asyncio
import json
import subprocess
import sys
from collections.abc import AsyncIterator
from unittest.mock import Mock

import httpx
import pytest
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

from code_agent.config import Settings
from code_agent.llm import client as client_module
from code_agent.llm.client import LLMClient


def test_cli_import_does_not_load_litellm() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import code_agent.ui.cli; assert 'litellm' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


async def test_sdk_preserves_overrides_reuses_transport_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "完成"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    sdk = AsyncOpenAI(api_key="test-key", base_url="https://default.invalid", http_client=http)
    factory = Mock(return_value=sdk)
    monkeypatch.setattr(client_module, "AsyncOpenAI", factory)
    client = LLMClient(
        Settings(_env_file=None, DEEPSEEK_API_KEY="test-key", base_url="https://default.invalid")
    )
    try:
        result = await client.complete_turn(
            [],
            model="deepseek/deepseek-chat",
            api_key="override-key",
            base_url="https://override.invalid",
            timeout=2.5,
        )
        assert result.choices[0].message.content == "完成"
        await client.complete_turn([])
        assert factory.call_count == 1
        assert factory.call_args.kwargs["max_retries"] == 0
        assert requests[0].url.host == "override.invalid"
        assert requests[0].headers["authorization"] == "Bearer override-key"
        assert requests[0].extensions["timeout"]["read"] == 2.5
        assert requests[1].url.host == "default.invalid"
        assert requests[1].headers["authorization"] == "Bearer test-key"
        assert requests[1].extensions["timeout"]["read"] == 60.0
        assert json.loads(requests[0].content)["model"] == "deepseek-chat"
    finally:
        await client.close()
    assert http.is_closed


async def test_sdk_does_not_retry_failed_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, json={"error": {"message": "server error"}})

    def factory(**kwargs: object) -> AsyncOpenAI:
        return AsyncOpenAI(
            **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))
        )

    monkeypatch.setattr(client_module, "AsyncOpenAI", factory)
    client = LLMClient(Settings(_env_file=None, DEEPSEEK_API_KEY="test-key"))
    try:
        with pytest.raises(APIStatusError):
            await client.complete_turn([])
        assert calls == 1
    finally:
        await client.close()


@pytest.mark.parametrize("ending", ["normal", "early-close", "cancel", "timeout", "disconnect"])
async def test_sdk_stream_closes_response_on_every_exit(
    monkeypatch: pytest.MonkeyPatch, ending: str
) -> None:
    waiting = asyncio.Event()
    closed = False

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            chunk = {
                "id": "test",
                "created": 0,
                "model": "deepseek-chat",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {"content": "部分正文"}, "finish_reason": None}],
            }
            yield ("data: " + json.dumps(chunk) + "\n\n").encode()
            if ending == "timeout":
                raise httpx.ReadTimeout("waiting for next chunk")
            if ending == "disconnect":
                raise httpx.ReadError("connection lost")
            if ending == "cancel":
                waiting.set()
                await asyncio.Event().wait()
            yield b"data: [DONE]\n\n"

        async def aclose(self) -> None:
            nonlocal closed
            closed = True

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Body())

    def factory(**kwargs: object) -> AsyncOpenAI:
        return AsyncOpenAI(
            **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))
        )

    monkeypatch.setattr(client_module, "AsyncOpenAI", factory)
    client = LLMClient(Settings(_env_file=None, DEEPSEEK_API_KEY="test-key"))
    stream = client.stream_turn([])
    task = None
    try:
        assert (await anext(stream)).choices[0].delta.content == "部分正文"
        if ending == "early-close":
            await stream.aclose()
        elif ending == "cancel":
            task = asyncio.create_task(anext(stream))
            await asyncio.wait_for(waiting.wait(), timeout=2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif ending in ("timeout", "disconnect"):
            error_type = APITimeoutError if ending == "timeout" else APIConnectionError
            with pytest.raises(error_type):
                await anext(stream)
        else:
            assert [chunk async for chunk in stream] == []
        assert closed
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await stream.aclose()
        await client.close()
