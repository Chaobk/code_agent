"""M2b：文本与工具分片组装，不请求模型、不执行工具。"""

from typing import Any

import pytest
from litellm.types.utils import ModelResponseStream

from code_agent.core.streaming import TurnAssembler


def text_chunk(
    content: str | None = None, *, finish_reason: str | None = None
) -> ModelResponseStream:
    return ModelResponseStream(
        choices=[{"delta": {"content": content}, "finish_reason": finish_reason}]
    )


def test_push_returns_only_new_text_and_finish_preserves_full_content() -> None:
    assembler = TurnAssembler()

    assert assembler.push(text_chunk("  我先")) == "  我先"
    assert assembler.push(text_chunk("查看代码\n")) == "查看代码\n"
    assert assembler.push(text_chunk(finish_reason="stop")) is None

    turn = assembler.finish()
    assert turn.finish_reason == "stop"
    assert turn.message == {"role": "assistant", "content": "  我先查看代码\n"}


def test_text_and_finish_reason_in_same_chunk_are_both_processed() -> None:
    assembler = TurnAssembler()

    assert assembler.push(text_chunk("完成", finish_reason="stop")) == "完成"

    turn = assembler.finish()
    assert turn.finish_reason == "stop"
    assert turn.message == {"role": "assistant", "content": "完成"}


def test_role_only_and_empty_text_chunks_do_not_produce_text() -> None:
    assembler = TurnAssembler()

    assert assembler.push(ModelResponseStream(choices=[{"delta": {"role": "assistant"}}])) is None
    assert assembler.push(text_chunk("")) is None
    assert assembler.push(text_chunk(finish_reason="stop")) is None
    assert assembler.finish().message == {"role": "assistant", "content": ""}


def test_empty_choices_including_trailing_usage_do_not_change_turn() -> None:
    assembler = TurnAssembler()

    assert assembler.push(ModelResponseStream(choices=[])) is None
    assert assembler.push(text_chunk("你好", finish_reason="stop")) == "你好"
    assert (
        assembler.push(
            ModelResponseStream(
                choices=[],
                usage={"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            )
        )
        is None
    )

    turn = assembler.finish()
    assert turn.finish_reason == "stop"
    assert turn.message == {"role": "assistant", "content": "你好"}


def test_finish_rejects_stream_without_finish_reason() -> None:
    assembler = TurnAssembler()
    assembler.push(text_chunk("尚未收完"))

    with pytest.raises(RuntimeError, match="结束"):
        assembler.finish()


def tool_fragment(
    index: int,
    arguments: str,
    *,
    call_id: str | None = None,
    name: str | None = None,
    call_type: str | None = None,
) -> dict[str, Any]:
    return {
        "index": index,
        "id": call_id,
        "type": call_type,
        "function": {"name": name, "arguments": arguments},
    }


def tool_chunk(
    *fragments: dict[str, Any],
    content: str | None = None,
    finish_reason: str | None = None,
) -> ModelResponseStream:
    return ModelResponseStream(
        choices=[
            {
                "delta": {"content": content, "tool_calls": list(fragments)},
                "finish_reason": finish_reason,
            }
        ]
    )


def test_tool_arguments_are_appended_and_preserved_in_finished_message() -> None:
    assembler = TurnAssembler()
    assert (
        assembler.push(
            tool_chunk(
                tool_fragment(
                    0, '{"command":', call_id="call_1", name="bash", call_type="function"
                ),
                content="先运行测试",
            )
        )
        == "先运行测试"
    )
    assert assembler.push(tool_chunk(tool_fragment(0, '"pytest"}'))) is None
    assert assembler.push(text_chunk(finish_reason="tool_calls")) is None

    turn = assembler.finish()
    assert turn.finish_reason == "tool_calls"
    assert turn.message == {
        "role": "assistant",
        "content": "先运行测试",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "bash", "arguments": '{"command":"pytest"}'},
            }
        ],
    }


def test_all_fragments_in_one_chunk_and_interleaved_calls_are_assembled_by_index() -> None:
    assembler = TurnAssembler()
    assembler.push(
        tool_chunk(
            tool_fragment(1, '{"command":', call_id="call_2", name="bash", call_type="function"),
            tool_fragment(0, '{"command":', call_id="call_1", name="bash", call_type="function"),
        )
    )
    assembler.push(tool_chunk(tool_fragment(0, '"pwd"}'), tool_fragment(1, '"pytest"}')))
    assembler.push(text_chunk(finish_reason="tool_calls"))

    calls = assembler.finish().message["tool_calls"]
    assert [call["id"] for call in calls] == ["call_1", "call_2"]
    assert [call["function"]["arguments"] for call in calls] == [
        '{"command":"pwd"}',
        '{"command":"pytest"}',
    ]


def test_invalid_tool_type_is_rejected() -> None:
    assembler = TurnAssembler()
    assembler.push(
        tool_chunk(tool_fragment(0, "", call_id="call_1", name="bash", call_type="function"))
    )
    invalid = tool_fragment(0, "", call_type="other")

    with pytest.raises(RuntimeError, match="类型"):
        assembler.push(tool_chunk(invalid))


@pytest.mark.parametrize("finish_reason", ["length", "content_filter"])
def test_finish_rejects_abnormal_reason(finish_reason: str) -> None:
    assembler = TurnAssembler()
    assembler.push(text_chunk("未完成", finish_reason=finish_reason))

    with pytest.raises(RuntimeError, match="未正常|异常"):
        assembler.finish()


def test_finish_rejects_tool_calls_reason_without_calls() -> None:
    assembler = TurnAssembler()
    assembler.push(text_chunk(finish_reason="tool_calls"))

    with pytest.raises(RuntimeError, match="没有.*调用|缺少.*调用|调用信息"):
        assembler.finish()
