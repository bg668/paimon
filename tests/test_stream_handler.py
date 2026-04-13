from __future__ import annotations

import asyncio

from paimonsdk.runtime.config import AgentLoopConfig
from paimonsdk.runtime.events import MessageEndEvent, MessageStartEvent, MessageUpdateEvent
from paimonsdk.runtime.models import (
    AgentContext,
    AssistantMessage,
    AssistantStreamDone,
    AssistantStreamStart,
    AssistantTextDelta,
    ModelInfo,
    TextContent,
    UserMessage,
)
from paimonsdk.runtime.stream_handler import stream_assistant_response


class FakeEventStream:
    def __init__(self, events, final_message: AssistantMessage) -> None:
        self._events = list(events)
        self._final_message = final_message

    def __aiter__(self):
        async def _iterate():
            for event in self._events:
                yield event

        return _iterate()

    async def result(self) -> AssistantMessage:
        return self._final_message


class DynamicFakeEventStream:
    def __init__(self, partial: AssistantMessage, final_message: AssistantMessage) -> None:
        self._partial = partial
        self._final_message = final_message

    def __aiter__(self):
        async def _iterate():
            self._partial.content.append(TextContent(text=""))
            yield AssistantStreamStart(partial=self._partial)
            self._partial.content[0].text = "Hel"
            yield AssistantTextDelta(partial=self._partial, delta="Hel", index=0)
            self._partial.content[0].text = "Hello"
            yield AssistantTextDelta(partial=self._partial, delta="lo", index=0)
            yield AssistantStreamDone(partial=self._final_message)

        return _iterate()

    async def result(self) -> AssistantMessage:
        return self._final_message


def _identity(messages):
    return list(messages)


def test_stream_handler_streaming_partial_message_converges_to_final_message():
    async def _run() -> None:
        partial = AssistantMessage(content=[], api="chat.completions", provider="openai", model="gpt-test")
        final = AssistantMessage(
            content=[TextContent(text="Hello")],
            stop_reason="stop",
            api="chat.completions",
            provider="openai",
            model="gpt-test",
        )

        async def fake_stream_fn(model, context, config, cancel_token):
            return DynamicFakeEventStream(partial, final)

        emitted = []

        async def emit(event):
            emitted.append(event)

        context = AgentContext(
            system_prompt="system",
            messages=[UserMessage(content=[TextContent(text="hi")])],
            tools=[],
        )
        config = AgentLoopConfig(
            model=ModelInfo(id="gpt-test", provider="openai", api="chat.completions"),
            stream_fn=fake_stream_fn,
            convert_to_llm=_identity,
        )

        result = await stream_assistant_response(context, config, emit)

        assert result == final
        assert len(context.messages) == 2
        assert context.messages[-1] == final
        assert [event.type for event in emitted] == [
            "message_start",
            "message_update",
            "message_update",
            "message_end",
        ]
        assert isinstance(emitted[0], MessageStartEvent)
        assert len(emitted[0].message.content) == 1
        assert emitted[0].message.content[0].text == ""
        assert isinstance(emitted[1], MessageUpdateEvent)
        assert emitted[1].message.content[0].text == "Hel"
        assert isinstance(emitted[2], MessageUpdateEvent)
        assert emitted[2].message.content[0].text == "Hello"
        assert isinstance(emitted[3], MessageEndEvent)
        assert emitted[3].message.content[0].text == "Hello"

    asyncio.run(_run())


def test_stream_handler_non_stream_result_uses_same_end_contract():
    async def _run() -> None:
        final = AssistantMessage(
            content=[TextContent(text="Final answer")],
            stop_reason="stop",
            api="chat.completions",
            provider="openai",
            model="gpt-test",
        )

        async def fake_stream_fn(model, context, config, cancel_token):
            return FakeEventStream([], final)

        transformed_inputs = []
        llm_inputs = []
        emitted = []

        async def transform_context(messages, cancel_token):
            transformed_inputs.append(list(messages))
            return list(messages)

        async def convert_to_llm(messages):
            llm_inputs.append(list(messages))
            return list(messages)

        async def emit(event):
            emitted.append(event)

        context = AgentContext(
            system_prompt="system",
            messages=[UserMessage(content=[TextContent(text="prompt")])],
            tools=[],
        )
        config = AgentLoopConfig(
            model=ModelInfo(id="gpt-test", provider="openai", api="chat.completions"),
            stream_fn=fake_stream_fn,
            transform_context=transform_context,
            convert_to_llm=convert_to_llm,
        )

        result = await stream_assistant_response(context, config, emit)

        assert result == final
        assert len(context.messages) == 2
        assert context.messages[-1] == final
        assert len(transformed_inputs) == 1
        assert len(llm_inputs) == 1
        assert [event.type for event in emitted] == ["message_start", "message_end"]
        assert emitted[0].message.content[0].text == "Final answer"
        assert emitted[1].message.content[0].text == "Final answer"

    asyncio.run(_run())
