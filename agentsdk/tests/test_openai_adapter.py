from __future__ import annotations

import asyncio
from types import SimpleNamespace

from ..adapters import OpenAIChatCompletionsAdapter
from ..runtime.config import AgentLoopConfig
from ..runtime.models import AgentContext, ModelInfo, TextContent, ToolCallContent, UserMessage


class FakeCompletionsClient:
    def __init__(self, response) -> None:
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class FakeOpenAIClient:
    def __init__(self, response) -> None:
        self.chat = SimpleNamespace(completions=FakeCompletionsClient(response))


class FakeAsyncChunkStream:
    def __init__(self, chunks) -> None:
        self._chunks = list(chunks)

    def __aiter__(self):
        async def _iterate():
            for chunk in self._chunks:
                yield chunk

        return _iterate()


def test_openai_adapter_create_message_maps_non_stream_response():
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
        client = FakeOpenAIClient(response)
        adapter = OpenAIChatCompletionsAdapter(client)
        model = ModelInfo(id="gpt-4o-mini", provider="openai", api="chat.completions")
        context = AgentContext(
            system_prompt="system",
            messages=[UserMessage(content=[TextContent(text="Say hello")])],
            tools=[],
        )
        config = AgentLoopConfig(model=model, stream_fn=adapter.stream_message, convert_to_llm=lambda messages: list(messages))

        message = await adapter.create_message(model, context, config)

        assert message.role == "assistant"
        assert message.content[0].text == "Hello from model"
        assert message.stop_reason == "stop"
        assert message.usage.input == 11
        assert message.usage.output == 7
        assert client.chat.completions.calls[0]["stream"] is False
        assert client.chat.completions.calls[0]["messages"][0] == {"role": "system", "content": "system"}
        assert client.chat.completions.calls[0]["messages"][1]["role"] == "user"

    asyncio.run(_run())


def test_openai_adapter_includes_system_prompt_in_messages():
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
        client = FakeOpenAIClient(response)
        adapter = OpenAIChatCompletionsAdapter(client)
        model = ModelInfo(id="gpt-4o-mini", provider="openai", api="chat.completions")
        context = AgentContext(
            system_prompt="You are a helpful assistant.",
            messages=[UserMessage(content=[TextContent(text="Say hello")])],
            tools=[],
        )
        config = AgentLoopConfig(model=model, stream_fn=adapter.stream_message, convert_to_llm=lambda messages: list(messages))

        await adapter.create_message(model, context, config)

        assert client.chat.completions.calls[0]["messages"][0] == {
            "role": "system",
            "content": "You are a helpful assistant.",
        }
        assert client.chat.completions.calls[0]["messages"][1]["role"] == "user"

    asyncio.run(_run())


def test_openai_adapter_stream_message_maps_deltas_and_tool_call_arguments():
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
        client = FakeOpenAIClient(FakeAsyncChunkStream(chunks))
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
        config = AgentLoopConfig(model=model, stream_fn=adapter.stream_message, convert_to_llm=lambda messages: list(messages))

        stream = await adapter.stream_message(model, context, config)
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
