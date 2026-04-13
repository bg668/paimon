from __future__ import annotations

import asyncio

from paimonsdk import (
    Agent,
    AgentOptions,
    AgentSession,
    AgentToolResult,
    Checkpoint,
    CheckpointImportError,
    ModelInfo,
    SessionExportError,
    TextContent,
    UserMessage,
)
from paimonsdk.runtime.models import AgentContext, AssistantMessage


class ImmediateFinalStream:
    def __init__(self, final_message: AssistantMessage) -> None:
        self._final_message = final_message

    def __aiter__(self):
        async def _iterate():
            if False:
                yield None

        return _iterate()

    async def result(self) -> AssistantMessage:
        return self._final_message


class DelayedFinalStream:
    def __init__(self, final_message: AssistantMessage, started: asyncio.Event, release: asyncio.Event) -> None:
        self._final_message = final_message
        self._started = started
        self._release = release

    def __aiter__(self):
        async def _iterate():
            self._started.set()
            await self._release.wait()
            if False:
                yield None

        return _iterate()

    async def result(self) -> AssistantMessage:
        return self._final_message


class EchoTool:
    name = "echo"
    label = "Echo"
    description = "Echo tool"
    input_schema = {"type": "object"}
    prepare_arguments = None

    async def execute(self, tool_call_id, params, cancel_token=None, on_update=None) -> AgentToolResult:
        return AgentToolResult(content=[TextContent(text="tool")], details={"tool_call_id": tool_call_id})


def _make_model() -> ModelInfo:
    return ModelInfo(id="gpt-test", provider="openai", api="chat.completions")


def _last_user_text(messages) -> str:
    for message in reversed(messages):
        if message.role != "user":
            continue
        for content in message.content:
            if content.type == "text":
                return content.text
    return ""


def test_session_roundtrip_restores_queues_and_continue_semantics():
    async def _run() -> None:
        model = _make_model()

        async def fake_stream_fn(model, context: AgentContext, config, cancel_token):
            return ImmediateFinalStream(
                AssistantMessage(
                    content=[TextContent(text=f"reply:{_last_user_text(context.messages)}")],
                    api=model.api,
                    provider=model.provider,
                    model=model.id,
                )
            )

        agent = Agent(AgentOptions(model=model, stream_fn=fake_stream_fn, tools=[EchoTool()]))
        await agent.prompt("seed")
        agent.steer(UserMessage(content=[TextContent(text="steer-again")]))
        agent.follow_up(UserMessage(content=[TextContent(text="follow-again")]))

        exported_session = agent.export_session()
        roundtripped_session = AgentSession.from_dict(exported_session.to_dict())

        assert roundtripped_session.tool_refs[0].name == "echo"
        assert roundtripped_session.steering_queue.messages[0].content[0].text == "steer-again"
        assert roundtripped_session.follow_up_queue.messages[0].content[0].text == "follow-again"
        assert roundtripped_session.stable_boundary.kind == "idle"

        restored = Agent.from_session(roundtripped_session, AgentOptions(model=model, stream_fn=fake_stream_fn))
        await restored.continue_()

        messages = restored.state.messages
        assert messages[2].role == "user"
        assert messages[2].content[0].text == "steer-again"
        assert messages[3].role == "assistant"
        assert messages[3].content[0].text == "reply:steer-again"
        assert messages[4].role == "user"
        assert messages[4].content[0].text == "follow-again"
        assert messages[5].role == "assistant"
        assert messages[5].content[0].text == "reply:follow-again"

    asyncio.run(_run())


def test_checkpoint_roundtrip_resume_continue_from_user_boundary():
    async def _run() -> None:
        model = _make_model()

        async def fake_stream_fn(model, context: AgentContext, config, cancel_token):
            return ImmediateFinalStream(
                AssistantMessage(
                    content=[TextContent(text=f"reply:{_last_user_text(context.messages)}")],
                    api=model.api,
                    provider=model.provider,
                    model=model.id,
                )
            )

        agent = Agent(
            AgentOptions(
                model=model,
                stream_fn=fake_stream_fn,
                messages=[UserMessage(content=[TextContent(text="resume-me")])],
            )
        )
        checkpoint = agent.export_checkpoint()
        restored_checkpoint = Checkpoint.from_dict(checkpoint.to_dict())

        restored = Agent.from_checkpoint(restored_checkpoint, AgentOptions(model=model, stream_fn=fake_stream_fn))
        await restored.continue_()

        assert restored.state.messages[0].content[0].text == "resume-me"
        assert restored.state.messages[1].role == "assistant"
        assert restored.state.messages[1].content[0].text == "reply:resume-me"

    asyncio.run(_run())


def test_replay_run_returns_only_one_run_log():
    async def _run() -> None:
        model = _make_model()

        async def fake_stream_fn(model, context: AgentContext, config, cancel_token):
            return ImmediateFinalStream(
                AssistantMessage(
                    content=[TextContent(text=f"reply:{_last_user_text(context.messages)}")],
                    api=model.api,
                    provider=model.provider,
                    model=model.id,
                )
            )

        agent = Agent(AgentOptions(model=model, stream_fn=fake_stream_fn))
        await agent.prompt("first")
        await agent.prompt("second")

        session = agent.export_session()
        run_ids = {event.run_id for event in session.event_log if event.run_id is not None}
        assert len(run_ids) == 2

        replayed = agent.replay_run()
        replay_run_ids = {event.run_id for event in replayed if event.run_id is not None}
        assert len(replay_run_ids) == 1
        assert replayed[0].type == "agent_start"
        assert replayed[-1].type == "agent_end"

        replayed_user_events = [
            event for event in replayed if event.type == "message_end" and event.payload["message"]["role"] == "user"
        ]
        assert replayed_user_events[0].payload["message"]["content"][0]["text"] == "second"

    asyncio.run(_run())


def test_multi_run_turn_index_monotonic():
    async def _run() -> None:
        model = _make_model()

        async def fake_stream_fn(model, context: AgentContext, config, cancel_token):
            return ImmediateFinalStream(
                AssistantMessage(
                    content=[TextContent(text=f"reply:{_last_user_text(context.messages)}")],
                    api=model.api,
                    provider=model.provider,
                    model=model.id,
                )
            )

        agent = Agent(AgentOptions(model=model, stream_fn=fake_stream_fn))
        await agent.prompt("first")
        await agent.prompt("second")

        session = agent.export_session()
        assert session.stable_boundary.last_turn_index == 2

        turn_events = [event for event in session.event_log if event.type in {"turn_start", "turn_end"}]
        assert [(event.type, event.turn_index) for event in turn_events] == [
            ("turn_start", 1),
            ("turn_end", 1),
            ("turn_start", 2),
            ("turn_end", 2),
        ]

    asyncio.run(_run())


def test_resume_turn_index_continues_from_checkpoint():
    async def _run() -> None:
        model = _make_model()

        async def fake_stream_fn(model, context: AgentContext, config, cancel_token):
            return ImmediateFinalStream(
                AssistantMessage(
                    content=[TextContent(text=f"reply:{_last_user_text(context.messages)}")],
                    api=model.api,
                    provider=model.provider,
                    model=model.id,
                )
            )

        agent = Agent(AgentOptions(model=model, stream_fn=fake_stream_fn))
        await agent.prompt("first")
        checkpoint = agent.export_checkpoint()

        restored = Agent.from_checkpoint(checkpoint, AgentOptions(model=model, stream_fn=fake_stream_fn))
        await restored.prompt("second")

        session = restored.export_session()
        assert session.stable_boundary.last_turn_index == 2

        turn_events = [event for event in session.event_log if event.type in {"turn_start", "turn_end"}]
        assert [(event.type, event.turn_index) for event in turn_events] == [
            ("turn_start", 1),
            ("turn_end", 1),
            ("turn_start", 2),
            ("turn_end", 2),
        ]

    asyncio.run(_run())


def test_export_session_and_checkpoint_require_stable_boundary():
    async def _run() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        model = _make_model()

        async def fake_stream_fn(model, context: AgentContext, config, cancel_token):
            return DelayedFinalStream(
                AssistantMessage(
                    content=[TextContent(text="done")],
                    api=model.api,
                    provider=model.provider,
                    model=model.id,
                ),
                started=started,
                release=release,
            )

        agent = Agent(AgentOptions(model=model, stream_fn=fake_stream_fn))
        task = asyncio.create_task(agent.prompt("running"))
        await started.wait()

        try:
            agent.export_session()
        except SessionExportError:
            pass
        else:
            raise AssertionError("Expected SessionExportError for export_session during an active run")

        try:
            agent.export_checkpoint()
        except SessionExportError:
            pass
        else:
            raise AssertionError("Expected SessionExportError for export_checkpoint during an active run")

        release.set()
        await task

    asyncio.run(_run())


def test_from_checkpoint_rejects_non_stable_boundary():
    model = _make_model()
    checkpoint = Agent(
        AgentOptions(
            model=model,
            messages=[UserMessage(content=[TextContent(text="seed")])],
        )
    ).export_checkpoint()
    checkpoint.stable_boundary.kind = "streaming"  # type: ignore[assignment]

    try:
        Agent.from_checkpoint(checkpoint, AgentOptions(model=model))
    except CheckpointImportError:
        pass
    else:
        raise AssertionError("Expected CheckpointImportError for a non-stable checkpoint boundary")


def test_from_checkpoint_rejects_top_level_boundary_tamper():
    async def _run() -> None:
        model = _make_model()

        async def fake_stream_fn(model, context: AgentContext, config, cancel_token):
            return ImmediateFinalStream(
                AssistantMessage(
                    content=[TextContent(text=f"reply:{_last_user_text(context.messages)}")],
                    api=model.api,
                    provider=model.provider,
                    model=model.id,
                )
            )

        agent = Agent(AgentOptions(model=model, stream_fn=fake_stream_fn))
        await agent.prompt("seed")

        checkpoint = agent.export_checkpoint()
        checkpoint.stable_boundary.last_run_id = "tampered-run"
        try:
            Agent.from_checkpoint(checkpoint, AgentOptions(model=model, stream_fn=fake_stream_fn))
        except CheckpointImportError as exc:
            assert str(exc) == "Checkpoint stable_boundary.last_run_id does not match session stable_boundary"
        else:
            raise AssertionError("Expected CheckpointImportError for tampered checkpoint last_run_id")

        checkpoint = agent.export_checkpoint()
        checkpoint.stable_boundary.last_turn_index += 1
        try:
            Agent.from_checkpoint(checkpoint, AgentOptions(model=model, stream_fn=fake_stream_fn))
        except CheckpointImportError as exc:
            assert str(exc) == "Checkpoint stable_boundary.last_turn_index does not match session stable_boundary"
        else:
            raise AssertionError("Expected CheckpointImportError for tampered checkpoint last_turn_index")

    asyncio.run(_run())


def test_from_checkpoint_rejects_session_boundary_event_log_mismatch():
    async def _run() -> None:
        model = _make_model()

        async def fake_stream_fn(model, context: AgentContext, config, cancel_token):
            return ImmediateFinalStream(
                AssistantMessage(
                    content=[TextContent(text=f"reply:{_last_user_text(context.messages)}")],
                    api=model.api,
                    provider=model.provider,
                    model=model.id,
                )
            )

        agent = Agent(AgentOptions(model=model, stream_fn=fake_stream_fn))
        await agent.prompt("seed")

        checkpoint = agent.export_checkpoint()
        checkpoint.session.stable_boundary.event_seq += 1
        checkpoint.stable_boundary.event_seq = checkpoint.session.stable_boundary.event_seq
        try:
            Agent.from_checkpoint(checkpoint, AgentOptions(model=model, stream_fn=fake_stream_fn))
        except CheckpointImportError as exc:
            assert str(exc) == "Session stable_boundary.event_seq does not match event log"
        else:
            raise AssertionError("Expected CheckpointImportError for mismatched session event_seq")

        checkpoint = agent.export_checkpoint()
        checkpoint.session.stable_boundary.last_turn_index += 1
        checkpoint.stable_boundary.last_turn_index = checkpoint.session.stable_boundary.last_turn_index
        try:
            Agent.from_checkpoint(checkpoint, AgentOptions(model=model, stream_fn=fake_stream_fn))
        except CheckpointImportError as exc:
            assert str(exc) == "Session stable_boundary.last_turn_index does not match event log"
        else:
            raise AssertionError("Expected CheckpointImportError for mismatched session last_turn_index")

    asyncio.run(_run())
