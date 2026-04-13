from __future__ import annotations

import asyncio
from dataclasses import dataclass

from paimonsdk import Agent, AgentOptions, AgentToolResult, TextContent
from paimonsdk.runtime.models import (
    AgentContext,
    AssistantMessage,
    AssistantStreamDone,
    AssistantStreamStart,
    AssistantTextDelta,
    ModelInfo,
    ToolCallContent,
)


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


@dataclass
class EchoTool:
    name: str = "echo"
    label: str = "Echo"
    description: str | None = "Echo tool"
    input_schema: dict | None = None
    prepare_arguments: callable | None = None

    async def execute(self, tool_call_id, params, cancel_token=None, on_update=None):
        if on_update is not None:
            on_update(AgentToolResult(content=[TextContent(text="working")], details={"phase": "update"}))
        return AgentToolResult(content=[TextContent(text="tool-result")], details={"tool_call_id": tool_call_id})


def test_event_protocol_envelope_order_and_replay_fields():
    async def _run() -> None:
        model = ModelInfo(id="gpt-test", provider="openai", api="chat.completions")

        async def fake_stream_fn(model, context: AgentContext, config, cancel_token):
            has_tool_result = any(message.role == "toolResult" for message in context.messages)
            if has_tool_result:
                final = AssistantMessage(
                    content=[TextContent(text="final answer")],
                    api=model.api,
                    provider=model.provider,
                    model=model.id,
                )
                return FakeEventStream([], final)

            partial = AssistantMessage(content=[TextContent(text="")], api=model.api, provider=model.provider, model=model.id)
            final = AssistantMessage(
                content=[
                    TextContent(text="need tool"),
                    ToolCallContent(id="call-1", name="echo", arguments={"x": 1}),
                ],
                stop_reason="tool_calls",
                api=model.api,
                provider=model.provider,
                model=model.id,
            )
            partial.content[0].text = ""
            return FakeEventStream(
                [
                    AssistantStreamStart(partial=partial),
                    AssistantTextDelta(partial=AssistantMessage(content=[TextContent(text="need tool")], api=model.api, provider=model.provider, model=model.id), delta="need tool", index=0),
                    AssistantStreamDone(partial=final),
                ],
                final,
            )

        agent = Agent(AgentOptions(model=model, stream_fn=fake_stream_fn, tools=[EchoTool()]))
        seen_events = []

        async def listener(event, cancel_token):
            seen_events.append(event)

        agent.subscribe(listener)
        await agent.prompt("run tool")

        assert [event.type for event in seen_events] == [
            "agent_start",
            "turn_start",
            "message_start",
            "message_end",
            "message_start",
            "message_update",
            "message_end",
            "tool_execution_start",
            "tool_execution_update",
            "tool_execution_end",
            "message_start",
            "message_end",
            "turn_end",
            "turn_start",
            "message_start",
            "message_end",
            "turn_end",
            "agent_end",
        ]

        run_ids = {event.run_id for event in seen_events}
        assert len(run_ids) == 1
        assert None not in run_ids
        assert [event.seq for event in seen_events] == list(range(1, len(seen_events) + 1))
        assert all(event.event_id for event in seen_events)
        assert all(event.timestamp > 0 for event in seen_events)
        assert all(event.turn_id is None for event in (seen_events[0], seen_events[-1]))
        assert all(event.turn_id is not None for event in seen_events[1:-1])

        first_turn_id = seen_events[1].turn_id
        second_turn_id = seen_events[13].turn_id
        assert first_turn_id != second_turn_id
        assert all(event.turn_id == first_turn_id for event in seen_events[1:13])
        assert all(event.turn_id == second_turn_id for event in seen_events[13:17])

        user_start = seen_events[2]
        user_end = seen_events[3]
        assistant_start = seen_events[4]
        assistant_update = seen_events[5]
        assistant_end = seen_events[6]
        tool_start = seen_events[7]
        tool_update = seen_events[8]
        tool_end = seen_events[9]
        turn_end = seen_events[12]

        assert user_start.message_id == user_end.message_id
        assert assistant_start.message_id == assistant_update.message_id == assistant_end.message_id
        assert tool_start.assistant_message_id == assistant_end.message_id
        assert tool_update.assistant_message_id == assistant_end.message_id
        assert tool_end.assistant_message_id == assistant_end.message_id
        assert turn_end.message_id == assistant_end.message_id

        replay = agent.replay_run()
        assert [event.type for event in replay] == [event.type for event in seen_events]
        assert [event.seq for event in replay] == [event.seq for event in seen_events]
        assert all(event.event_id for event in replay)
        assert all(event.run_id == seen_events[0].run_id for event in replay)
        assert [event.turn_index for event in replay if event.type in {"turn_start", "turn_end"}] == [1, 1, 2, 2]
        assert replay[2].payload["message_id"] == user_start.message_id
        assert replay[7].payload["tool_call_id"] == "call-1"
        assert replay[7].payload["assistant_message_id"] == assistant_end.message_id
        assert replay[12].payload["message_id"] == assistant_end.message_id

    asyncio.run(_run())


def test_event_protocol_turn_index_stays_monotonic_across_runs():
    async def _run() -> None:
        model = ModelInfo(id="gpt-test", provider="openai", api="chat.completions")

        async def fake_stream_fn(model, context: AgentContext, config, cancel_token):
            final = AssistantMessage(
                content=[TextContent(text="ok")],
                api=model.api,
                provider=model.provider,
                model=model.id,
            )
            return FakeEventStream([], final)

        agent = Agent(AgentOptions(model=model, stream_fn=fake_stream_fn))
        await agent.prompt("first")
        await agent.prompt("second")

        replay = agent.replay_run()
        turn_events = [event for event in agent.export_session().event_log if event.type in {"turn_start", "turn_end"}]
        assert [(event.type, event.turn_index) for event in turn_events] == [
            ("turn_start", 1),
            ("turn_end", 1),
            ("turn_start", 2),
            ("turn_end", 2),
        ]
        assert [event.turn_index for event in replay if event.type in {"turn_start", "turn_end"}] == [2, 2]

    asyncio.run(_run())
