"""Local protocol tests for OpenAI-compatible thinking/tool conversations."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from agent_coach.contracts import ToolDefinition, ToolOutcome, ToolRegistry
from agent_coach.engine import AgentEngine
from agent_coach.legacy.core.llm.openai_compat import OpenAICompatibleClient, to_openai_messages
from agent_coach.legacy.core.schema import Message, ToolSpec
from agent_coach.store import SessionStore


class _FakeDeepSeek:
    """A tiny local chat-completions endpoint that records every request."""

    def __init__(self):
        self.requests: list[dict[str, Any]] = []
        self.responses = [
            {
                "id": "chatcmpl-tool",
                "object": "chat.completion",
                "created": 1,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "reasoning_content": "tool reasoning must be echoed",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "lookup",
                                        "arguments": "{\"value\": \"ok\"}",
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
            {
                "id": "chatcmpl-final",
                "object": "chat.completion",
                "created": 2,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "final answer",
                            "reasoning_content": "final reasoning is durable too",
                            "tool_calls": None,
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
            },
        ]
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - stdlib HTTP handler API
                length = int(self.headers.get("content-length", "0"))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                owner.requests.append(body)
                index = len(owner.requests) - 1
                if index >= len(owner.responses):
                    self.send_error(500, "unexpected request")
                    return
                payload = json.dumps(owner.responses[index]).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/v1"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()


def test_openai_wire_preserves_and_can_explicitly_omit_reasoning():
    message = Message(
        role="assistant",
        content="",
        reasoning_content="opaque provider trace",
    )

    wire = to_openai_messages([message])
    assert wire[0]["reasoning_content"] == "opaque provider trace"

    without_extension = to_openai_messages([message], include_reasoning_content=False)
    assert "reasoning_content" not in without_extension[0]


def test_deepseek_tool_round_trip_keeps_reasoning_across_store_restore(tmp_path):
    with _FakeDeepSeek() as fake:
        store = SessionStore(tmp_path)
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                spec=ToolSpec(
                    name="lookup",
                    description="return a deterministic local result",
                    parameters={"type": "object", "properties": {}},
                ),
                handler=lambda _arguments, _context: ToolOutcome("lookup result"),
            )
        )
        client = OpenAICompatibleClient(
            base_url=fake.base_url,
            api_key="local-test-key",
            model="deepseek-chat",
        )

        class Router:
            def complete(self, _task, messages, *, tools, **kwargs):
                return client.complete(messages, tools=tools, **kwargs)

        engine = AgentEngine(store, registry, Router(), max_steps=4)
        result = engine.run("look this up")

        assert result.status == "completed"
        assert len(fake.requests) == 2
        second_messages = fake.requests[1]["messages"]
        assistant_with_tool = next(
            item
            for item in second_messages
            if item.get("role") == "assistant" and item.get("tool_calls")
        )
        assert assistant_with_tool["reasoning_content"] == "tool reasoning must be echoed"
        assert not any(
            item.get("role") == "tool" and "reasoning_content" in item
            for item in second_messages
        )

        assistant_messages = [m for m in result.session.messages if m.role == "assistant"]
        assert [m.reasoning_content for m in assistant_messages] == [
            "tool reasoning must be echoed",
            "final reasoning is durable too",
        ]

        # A fresh store instance validates the actual JSON recovery boundary,
        # rather than only checking the in-memory Pydantic object.
        restored = SessionStore(tmp_path).get(result.session.id)
        restored_assistants = [m for m in restored.messages if m.role == "assistant"]
        assert [m.reasoning_content for m in restored_assistants] == [
            "tool reasoning must be echoed",
            "final reasoning is durable too",
        ]


def test_old_session_message_without_reasoning_is_still_valid(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create()
    path = tmp_path / ".agent-coach" / "sessions" / f"{session.id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["messages"] = [{"role": "assistant", "content": "legacy"}]
    path.write_text(json.dumps(payload), encoding="utf-8")

    restored = store.get(session.id)
    assert restored.messages[0].reasoning_content is None


def test_switching_to_strict_openai_endpoint_omits_deepseek_reasoning():
    """Persisted DeepSeek traces must not be sent to another endpoint."""

    with _FakeDeepSeek() as fake:
        strict = OpenAICompatibleClient(
            base_url=fake.base_url,
            api_key="local-test-key",
            model="deepseek-chat",
            provider_name="strict-openai",
        )
        response = strict.complete(
            [
                Message(role="user", content="继续"),
                Message(
                    role="assistant",
                    content="",
                    reasoning_content="old DeepSeek trace",
                    reasoning_provider="deepseek",
                    tool_calls=[],
                ),
            ]
        )

        # The strict endpoint's own response is retained with its exact
        # endpoint/model provenance; only the old DeepSeek trace is filtered
        # from the request above.
        assert response.reasoning_content == "tool reasoning must be echoed"
        assert response.reasoning_provider
        assert response.reasoning_provider.startswith("openai-compat:")
        assert not any(
            "reasoning_content" in item for item in fake.requests[0]["messages"]
        )
        strict.complete(
            [
                Message(role="user", content="再次继续"),
                Message(
                    role="assistant",
                    content=response.text,
                    reasoning_content=response.reasoning_content,
                    reasoning_provider=response.reasoning_provider,
                ),
            ]
        )
        assert any(
            item.get("reasoning_content") == "tool reasoning must be echoed"
            for item in fake.requests[1]["messages"]
        )


def test_legacy_unmarked_deepseek_trace_still_round_trips_on_deepseek():
    """Sessions written before provenance existed remain DeepSeek-compatible."""

    with _FakeDeepSeek() as fake:
        client = OpenAICompatibleClient(
            base_url=fake.base_url,
            api_key="local-test-key",
            model="deepseek-chat",
        )
        client.complete(
            [
                Message(role="user", content="继续"),
                Message(
                    role="assistant",
                    content="",
                    reasoning_content="legacy DeepSeek trace",
                    tool_calls=[],
                ),
            ]
        )

        assistant = next(
            item
            for item in fake.requests[0]["messages"]
            if item.get("role") == "assistant"
        )
        assert assistant["reasoning_content"] == "legacy DeepSeek trace"
