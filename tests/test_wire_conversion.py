"""不依赖网络的 wire-format 转换测试。"""

from coach.core.llm.anthropic_adapter import to_anthropic_messages, to_anthropic_tools
from coach.core.llm.openai_compat import to_openai_messages, to_openai_tools
from coach.core.schema import (
    ImageBlock,
    Message,
    TextBlock,
    ToolCall,
    ToolSpec,
)


def test_openai_plain_text():
    wire = to_openai_messages([Message(role="user", content="你好")])
    assert wire == [{"role": "user", "content": "你好"}]


def test_openai_image_base64_becomes_data_url():
    msg = Message(
        role="user",
        content=[
            TextBlock(text="看图"),
            ImageBlock(media_type="image/jpeg", data="AAA="),
        ],
    )
    wire = to_openai_messages([msg])[0]
    assert wire["content"][0] == {"type": "text", "text": "看图"}
    assert wire["content"][1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/jpeg;base64,AAA="},
    }


def test_openai_image_url_passthrough():
    msg = Message(
        role="user",
        content=[ImageBlock(media_type="image/png", url="https://x.com/a.png")],
    )
    wire = to_openai_messages([msg])[0]
    assert wire["content"][0]["image_url"]["url"] == "https://x.com/a.png"


def test_openai_tool_call_roundtrip():
    msgs = [
        Message(role="user", content="q"),
        Message(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="c1", name="emit_result", arguments={"a": 1})],
        ),
        Message(role="tool", tool_call_id="c1", content="ok"),
    ]
    wire = to_openai_messages(msgs)
    assert wire[1]["tool_calls"][0]["function"]["name"] == "emit_result"
    assert '"a": 1' in wire[1]["tool_calls"][0]["function"]["arguments"]
    assert wire[2] == {"role": "tool", "content": "ok", "tool_call_id": "c1"}


def test_openai_tools_schema():
    wire = to_openai_tools(
        [ToolSpec(name="t", description="d", parameters={"type": "object"})]
    )
    assert wire[0]["type"] == "function"
    assert wire[0]["function"]["parameters"] == {"type": "object"}


# ── Anthropic ──────────────────────────────────────────────────

def test_anthropic_system_extracted():
    system, wire = to_anthropic_messages(
        [
            Message(role="system", content="你是引擎"),
            Message(role="user", content="hi"),
        ]
    )
    assert system == "你是引擎"
    assert wire == [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]


def test_anthropic_merges_adjacent_same_role():
    _, wire = to_anthropic_messages(
        [
            Message(role="user", content="第一句"),
            Message(role="user", content="第二句"),
            Message(role="assistant", content="回答"),
        ]
    )
    assert len(wire) == 2
    assert wire[0]["role"] == "user"
    assert len(wire[0]["content"]) == 2


def test_anthropic_tool_result_goes_under_user():
    _, wire = to_anthropic_messages(
        [
            Message(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id="c1", name="f", arguments={"x": 1})],
            ),
            Message(role="tool", tool_call_id="c1", content="结果"),
        ]
    )
    assert wire[0]["role"] == "assistant"
    assert wire[0]["content"][0] == {
        "type": "tool_use",
        "id": "c1",
        "name": "f",
        "input": {"x": 1},
    }
    assert wire[1]["role"] == "user"
    assert wire[1]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "c1",
        "content": "结果",
    }


def test_anthropic_image_block():
    _, wire = to_anthropic_messages(
        [
            Message(
                role="user",
                content=[ImageBlock(media_type="image/png", data="AAA=")],
            )
        ]
    )
    assert wire[0]["content"][0] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "AAA="},
    }


def test_anthropic_tools_use_input_schema():
    wire = to_anthropic_tools(
        [ToolSpec(name="t", description="d", parameters={"type": "object"})]
    )
    assert wire[0] == {
        "name": "t",
        "description": "d",
        "input_schema": {"type": "object"},
    }
