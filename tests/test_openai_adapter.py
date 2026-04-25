from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from paimonsdk.adapters import (
    OpenAIAdapter,
    OpenAIChatCompletionsAdapter,
    OpenAIRequestConfig,
    OpenAIResponsesAdapter,
)
from paimonsdk.runtime.config import AgentLoopConfig
from paimonsdk.runtime.errors import OpenAIAdapterError
from paimonsdk.runtime.models import (
    AgentContext,
    ModelInfo,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolExecutionMode,
    ToolResultMessage,
    UserMessage,
)


class FakeResourceClient:
    def __init__(self, response=None) -> None:
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class FakeOpenAIClient:
    def __init__(self, *, chat_response=None, responses_response=None) -> None:
        self.chat = SimpleNamespace(completions=FakeResourceClient(chat_response))
        self.responses = FakeResourceClient(responses_response)


class FakeAsyncChunkStream:
    def __init__(self, chunks) -> None:
        self._chunks = list(chunks)

    def __aiter__(self):
        async def _iterate():
            for chunk in self._chunks:
                yield chunk

        return _iterate()


def _loop_config(adapter, model: ModelInfo, *, tool_execution: ToolExecutionMode = ToolExecutionMode.PARALLEL):
    return AgentLoopConfig(
        model=model,
        stream_fn=adapter.stream_message,
        convert_to_llm=lambda messages: list(messages),
        tool_execution=tool_execution,
    )


def test_chat_adapter_create_message_maps_non_stream_response():
    async def _run() -> None:
        response = SimpleNamespace(
            id="chatcmpl_1",
            model="gpt-4o-mini",
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18),
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content="Hello from model", tool_calls=[]),
                )
            ],
        )
        client = FakeOpenAIClient(chat_response=response)
        adapter = OpenAIChatCompletionsAdapter(client)
        model = ModelInfo(id="gpt-4o-mini", provider="openai", api="chat.completions")
        context = AgentContext(
            system_prompt="system",
            messages=[UserMessage(content=[TextContent(text="Say hello")])],
            tools=[],
        )

        message = await adapter.create_message(model, context, _loop_config(adapter, model))

        assert message.role == "assistant"
        assert message.content[0].text == "Hello from model"
        assert message.stop_reason == "stop"
        assert message.usage.input == 11
        assert message.usage.output == 7
        assert client.chat.completions.calls[0]["stream"] is False
        assert client.chat.completions.calls[0]["messages"][0] == {"role": "system", "content": "system"}
        assert client.chat.completions.calls[0]["messages"][1]["role"] == "user"

    asyncio.run(_run())


def test_chat_adapter_includes_system_prompt_in_messages():
    async def _run() -> None:
        response = SimpleNamespace(
            id="chatcmpl_1",
            model="gpt-4o-mini",
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18),
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content="Hello from model", tool_calls=[]),
                )
            ],
        )
        client = FakeOpenAIClient(chat_response=response)
        adapter = OpenAIChatCompletionsAdapter(client)
        model = ModelInfo(id="gpt-4o-mini", provider="openai", api="chat.completions")
        context = AgentContext(
            system_prompt="You are a helpful assistant.",
            messages=[UserMessage(content=[TextContent(text="Say hello")])],
            tools=[],
        )

        await adapter.create_message(model, context, _loop_config(adapter, model))

        assert client.chat.completions.calls[0]["messages"][0] == {
            "role": "system",
            "content": "You are a helpful assistant.",
        }
        assert client.chat.completions.calls[0]["messages"][1]["role"] == "user"

    asyncio.run(_run())


def test_chat_adapter_stream_message_maps_deltas_and_tool_call_arguments():
    async def _run() -> None:
        chunks = [
            {
                "choices": [
                    {
                        "delta": {"role": "assistant", "content": "Hi"},
                        "finish_reason": None,
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "function": {"name": "search", "arguments": '{"q":"hel'},
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "function": {"arguments": 'lo"}'},
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ]
            },
            {
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
                "choices": [
                    {
                        "delta": {},
                        "finish_reason": "tool_calls",
                    }
                ],
            },
        ]
        client = FakeOpenAIClient(chat_response=FakeAsyncChunkStream(chunks))
        adapter = OpenAIChatCompletionsAdapter(client)
        model = ModelInfo(id="gpt-4o-mini", provider="openai", api="chat.completions")
        tool = SimpleNamespace(
            name="search",
            description="Search the web",
            input_schema={"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
        )
        context = AgentContext(
            system_prompt="system",
            messages=[UserMessage(content=[TextContent(text="Find hello")])],
            tools=[tool],
        )

        stream = await adapter.stream_message(model, context, _loop_config(adapter, model))
        events = []
        async for event in stream:
            events.append(event)
        final_message = await stream.result()

        assert [event.type for event in events] == ["start", "text_delta", "tool_call_delta", "tool_call_delta", "done"]
        assert events[1].delta == "Hi"
        assert events[2].tool_call.name == "search"
        assert events[2].tool_call.arguments == {"q": "hel"}
        assert events[3].tool_call.arguments == {"q": "hello"}
        assert final_message.stop_reason == "tool_calls"
        assert final_message.usage.total_tokens == 8
        assert final_message.content[0] == TextContent(text="Hi")
        assert final_message.content[1] == ToolCallContent(id="call_1", name="search", arguments={"q": "hello"})
        assert client.chat.completions.calls[0]["stream"] is True
        assert client.chat.completions.calls[0]["tools"][0]["function"]["name"] == "search"

    asyncio.run(_run())


def test_chat_adapter_request_config_owns_provider_specific_options():
    async def _run() -> None:
        response = SimpleNamespace(
            id="chatcmpl_1",
            model="gpt-4o-mini",
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18),
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content="Hello from model", tool_calls=[]),
                )
            ],
        )
        client = FakeOpenAIClient(chat_response=response)
        resolver_calls = []

        async def resolve_api_key(provider: str) -> str:
            resolver_calls.append(provider)
            return "resolved-key"

        adapter = OpenAIChatCompletionsAdapter(
            client,
            request_config=OpenAIRequestConfig(
                api_key_resolver=resolve_api_key,
                temperature=0.2,
                top_p=0.9,
                max_tokens=256,
                metadata={"trace_id": "req-1"},
            ),
        )
        model = ModelInfo(id="gpt-4o-mini", provider="openai", api="chat.completions")
        context = AgentContext(
            system_prompt="system",
            messages=[UserMessage(content=[TextContent(text="Say hello")])],
            tools=[],
        )

        await adapter.create_message(model, context, _loop_config(adapter, model))

        assert resolver_calls == ["openai"]
        assert client.chat.completions.calls[0]["api_key"] == "resolved-key"
        assert client.chat.completions.calls[0]["temperature"] == 0.2
        assert client.chat.completions.calls[0]["top_p"] == 0.9
        assert client.chat.completions.calls[0]["max_tokens"] == 256
        assert client.chat.completions.calls[0]["metadata"] == {"trace_id": "req-1"}

    asyncio.run(_run())


def test_chat_adapter_rejects_non_chat_api():
    async def _run() -> None:
        client = FakeOpenAIClient(chat_response=None)
        adapter = OpenAIChatCompletionsAdapter(client)
        model = ModelInfo(id="gpt-4o-mini", provider="openai", api="responses")
        context = AgentContext(messages=[UserMessage(content=[TextContent(text="hi")])])

        message = await adapter.create_message(model, context, _loop_config(adapter, model))

        assert message.stop_reason == "error"
        assert "only supports model.api='chat.completions'" in (message.error_message or "")

    asyncio.run(_run())


def test_openai_adapter_dispatches_to_chat_implementation():
    async def _run() -> None:
        chat_response = SimpleNamespace(
            model="gpt-4o-mini",
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="chat", tool_calls=[]))],
        )
        client = FakeOpenAIClient(chat_response=chat_response)
        adapter = OpenAIAdapter(client)
        model = ModelInfo(id="gpt-4o-mini", provider="openai", api="chat.completions")
        context = AgentContext(messages=[UserMessage(content=[TextContent(text="hi")])])

        message = await adapter.create_message(model, context, _loop_config(adapter, model))

        assert message.content[0].text == "chat"
        assert len(client.chat.completions.calls) == 1
        assert len(client.responses.calls) == 0

    asyncio.run(_run())


def test_openai_adapter_dispatches_to_responses_implementation():
    async def _run() -> None:
        response = {
            "model": "gpt-4.1-mini",
            "status": "completed",
            "usage": {"input_tokens": 2, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 1, "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 3},
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "responses"}]}],
        }
        client = FakeOpenAIClient(responses_response=response)
        adapter = OpenAIAdapter(client)
        model = ModelInfo(id="gpt-4.1-mini", provider="openai", api="responses")
        context = AgentContext(messages=[UserMessage(content=[TextContent(text="hi")])])

        message = await adapter.create_message(model, context, _loop_config(adapter, model))

        assert message.content[0].text == "responses"
        assert len(client.responses.calls) == 1
        assert len(client.chat.completions.calls) == 0

    asyncio.run(_run())


def test_openai_adapter_rejects_unknown_api():
    adapter = OpenAIAdapter(FakeOpenAIClient())
    with pytest.raises(OpenAIAdapterError, match="Unsupported OpenAI model.api"):
        asyncio.run(
            adapter.create_message(
                ModelInfo(id="gpt-test", provider="openai", api="unknown"),
                AgentContext(messages=[UserMessage(content=[TextContent(text="hi")])]),
                _loop_config(adapter, ModelInfo(id="gpt-test", provider="openai", api="unknown")),
            )
        )


def test_responses_adapter_maps_non_stream_response():
    async def _run() -> None:
        response = {
            "model": "gpt-4.1-mini",
            "status": "completed",
            "usage": {
                "input_tokens": 11,
                "input_tokens_details": {"cached_tokens": 2},
                "output_tokens": 7,
                "output_tokens_details": {"reasoning_tokens": 3},
                "total_tokens": 18,
            },
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "Hello from responses"}]},
                {"type": "reasoning", "summary": [{"type": "summary_text", "text": "I should call a tool."}]},
                {"type": "function_call", "call_id": "call_1", "name": "search", "arguments": '{"q":"hello"}'},
            ],
        }
        client = FakeOpenAIClient(responses_response=response)
        adapter = OpenAIResponsesAdapter(client)
        model = ModelInfo(id="gpt-4.1-mini", provider="openai", api="responses")
        context = AgentContext(messages=[UserMessage(content=[TextContent(text="hello")])])

        message = await adapter.create_message(model, context, _loop_config(adapter, model))

        assert message.stop_reason == "tool_calls"
        assert message.usage.input == 11
        assert message.usage.output == 7
        assert message.usage.cache_read == 2
        assert message.content[0] == TextContent(text="Hello from responses")
        assert message.content[1] == ThinkingContent(thinking="I should call a tool.")
        assert message.content[2] == ToolCallContent(id="call_1", name="search", arguments={"q": "hello"})

    asyncio.run(_run())


def test_responses_adapter_stream_message_maps_text_reasoning_and_tool_calls():
    async def _run() -> None:
        stream_events = [
            {"type": "response.output_item.added", "output_index": 2, "item": {"type": "function_call", "call_id": "call_1", "name": "search", "arguments": ""}},
            {"type": "response.output_text.delta", "output_index": 0, "content_index": 0, "delta": "Hi"},
            {"type": "response.reasoning_text.delta", "output_index": 1, "content_index": 0, "delta": "Need tool"},
            {"type": "response.function_call_arguments.delta", "output_index": 2, "item_id": "fc_1", "delta": '{"q":"hel'},
            {"type": "response.function_call_arguments.done", "output_index": 2, "item_id": "fc_1", "name": "search", "arguments": '{"q":"hello"}'},
            {
                "type": "response.completed",
                "response": {
                    "model": "gpt-4.1-mini",
                    "status": "completed",
                    "usage": {
                        "input_tokens": 5,
                        "input_tokens_details": {"cached_tokens": 0},
                        "output_tokens": 3,
                        "output_tokens_details": {"reasoning_tokens": 1},
                        "total_tokens": 8,
                    },
                    "output": [
                        {"type": "message", "content": [{"type": "output_text", "text": "Hi"}]},
                        {"type": "reasoning", "summary": [{"type": "summary_text", "text": "Need tool"}]},
                        {"type": "function_call", "call_id": "call_1", "name": "search", "arguments": '{"q":"hello"}'},
                    ],
                },
            },
        ]
        client = FakeOpenAIClient(responses_response=FakeAsyncChunkStream(stream_events))
        adapter = OpenAIResponsesAdapter(client)
        model = ModelInfo(id="gpt-4.1-mini", provider="openai", api="responses")
        context = AgentContext(messages=[UserMessage(content=[TextContent(text="hello")])])

        stream = await adapter.stream_message(model, context, _loop_config(adapter, model))
        events = []
        async for event in stream:
            events.append(event)
        final_message = await stream.result()

        assert [event.type for event in events] == ["start", "text_delta", "thinking_delta", "tool_call_delta", "done"]
        assert events[1].delta == "Hi"
        assert events[2].delta == "Need tool"
        assert events[3].tool_call.name == "search"
        assert events[3].tool_call.arguments == {"q": "hel"}
        assert final_message.stop_reason == "tool_calls"
        assert final_message.usage.total_tokens == 8
        assert final_message.content[0] == TextContent(text="Hi")
        assert final_message.content[1] == ThinkingContent(thinking="Need tool")
        assert final_message.content[2] == ToolCallContent(id="call_1", name="search", arguments={"q": "hello"})

    asyncio.run(_run())


def test_responses_adapter_builds_requests_from_transcript_and_config():
    async def _run() -> None:
        response = {
            "model": "gpt-4.1-mini",
            "status": "completed",
            "usage": {
                "input_tokens": 3,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens": 1,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 4,
            },
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "done"}]}],
        }
        client = FakeOpenAIClient(responses_response=response)
        resolver_calls = []

        async def resolve_api_key(provider: str) -> str:
            resolver_calls.append(provider)
            return "resolved-key"

        adapter = OpenAIResponsesAdapter(
            client,
            request_config=OpenAIRequestConfig(
                api_key_resolver=resolve_api_key,
                temperature=0.2,
                top_p=0.9,
                max_tokens=256,
                metadata={"trace_id": "req-1"},
            ),
        )
        model = ModelInfo(id="gpt-4.1-mini", provider="openai", api="responses")
        tool = SimpleNamespace(
            name="search",
            description="Search the web",
            input_schema={"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
        )
        assistant_message = SimpleNamespace(
            role="assistant",
            content=[TextContent(text="Calling search"), ToolCallContent(id="call_1", name="search", arguments={"q": "hello"})],
        )
        tool_result = ToolResultMessage(tool_call_id="call_1", tool_name="search", content=[TextContent(text="world")])
        context = AgentContext(
            system_prompt="system prompt",
            messages=[
                UserMessage(content=[TextContent(text="hello"),]),
                assistant_message,
                tool_result,
            ],
            tools=[tool],
        )
        config = _loop_config(adapter, model, tool_execution=ToolExecutionMode.SEQUENTIAL)
        config.metadata["run_id"] = "run-1"

        await adapter.create_message(model, context, config)

        assert resolver_calls == ["openai"]
        call = client.responses.calls[0]
        assert call["instructions"] == "system prompt"
        assert call["api_key"] == "resolved-key"
        assert call["temperature"] == 0.2
        assert call["top_p"] == 0.9
        assert call["max_output_tokens"] == 256
        assert call["parallel_tool_calls"] is False
        assert call["metadata"] == {"trace_id": "req-1", "run_id": "run-1"}
        assert call["tools"][0]["type"] == "function"
        assert call["input"][0] == {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "hello"}],
        }
        assert call["input"][1] == {
            "type": "message",
            "role": "assistant",
            "content": "Calling search",
        }
        assert call["input"][2] == {
            "type": "function_call",
            "call_id": "call_1",
            "name": "search",
            "arguments": '{"q":"hello"}',
        }
        assert call["input"][3] == {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": "world",
        }

    asyncio.run(_run())
