from __future__ import annotations

import asyncio
from collections import deque
from copy import deepcopy
from dataclasses import replace
from inspect import isawaitable
from typing import Any, Awaitable, Callable, Sequence
from uuid import uuid4

from .config import AgentLoopConfig, AgentOptions
from .errors import (
    AgentAlreadyRunningError,
    CheckpointImportError,
    InvalidContinuationError,
    ListenerOutsideRunError,
    SessionExportError,
)
from .events import (
    AgentEndEvent,
    AgentEvent,
    AgentStartEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
    enrich_event,
)
from .loop import run_agent_loop, run_agent_loop_continue
from .models import AgentContext, AgentMessage, AssistantMessage, ImageContent, TextContent, TokenUsage, UserMessage
from .queues import PendingMessageQueue
from .run_control import CancelToken, RunHandle
from .session import (
    AgentSession,
    Checkpoint,
    QueueSnapshot,
    RecordedEvent,
    StableBoundary,
    build_tool_references,
    replay_events,
    serialize_content_block,
    serialize_message,
    serialize_tool_result,
)
from .state import AgentStateView, MutableAgentState


def _default_convert_to_llm(messages: Sequence[AgentMessage]) -> list[AgentMessage]:
    return [message for message in messages if message.role in {"user", "assistant", "toolResult"}]


async def _missing_stream_fn(model, context, config, cancel_token):
    raise RuntimeError("No stream_fn configured for Agent")


async def _maybe_await(value: Awaitable[Any] | Any) -> Any:
    if isawaitable(value):
        return await value
    return value


class Agent:
    def __init__(self, options: AgentOptions | None = None) -> None:
        options = options or AgentOptions()
        session = deepcopy(options.session) if options.session is not None else None

        if session is None:
            system_prompt = options.system_prompt
            model = options.model
            thinking_level = options.thinking_level
            messages = list(options.messages)
            steering_mode = options.steering_mode
            steering_messages: list[AgentMessage] = []
            follow_up_mode = options.follow_up_mode
            follow_up_messages: list[AgentMessage] = []
            metadata = dict(options.metadata)
            self._event_log: list[RecordedEvent] = []
            self._event_seq = 0
            self._last_run_id: str | None = None
            self._completed_turn_index = 0
            self._stable_boundary = StableBoundary(
                kind="initialized" if not messages else "idle",
                message_count=len(messages),
            )
            self._session_id: str | None = None
        else:
            system_prompt = session.system_prompt
            model = session.model
            thinking_level = session.thinking_level
            messages = list(session.messages)
            steering_mode = session.steering_queue.mode
            steering_messages = list(session.steering_queue.messages)
            follow_up_mode = session.follow_up_queue.mode
            follow_up_messages = list(session.follow_up_queue.messages)
            metadata = dict(session.metadata)
            metadata.update(options.metadata)
            self._event_log = deepcopy(session.event_log)
            self._event_seq = max((event.seq for event in self._event_log), default=0)
            self._last_run_id = session.stable_boundary.last_run_id
            self._completed_turn_index = session.stable_boundary.last_turn_index
            self._stable_boundary = deepcopy(session.stable_boundary)
            self._session_id = session.session_id

        self._state = MutableAgentState(
            system_prompt=system_prompt,
            model=model,
            thinking_level=thinking_level,
            tools=options.tools,
            messages=messages,
        )
        self._listeners: list[Callable[[AgentEvent, CancelToken | None], Awaitable[None] | None]] = []
        self._steering_queue = PendingMessageQueue(steering_mode, steering_messages)
        self._follow_up_queue = PendingMessageQueue(follow_up_mode, follow_up_messages)
        self._active_run: RunHandle | None = None
        self._current_turn_index = self._completed_turn_index
        self._current_turn_id: str | None = None
        self._open_message_ids: deque[str] = deque()
        self._current_assistant_message_id: str | None = None

        self.convert_to_llm = options.convert_to_llm or _default_convert_to_llm
        self.transform_context = options.transform_context
        self.stream_fn = options.stream_fn or _missing_stream_fn
        self.before_tool_call = options.before_tool_call
        self.after_tool_call = options.after_tool_call
        self.tool_execution = options.tool_execution
        self.metadata = metadata

    @classmethod
    def from_session(cls, session: AgentSession, options: AgentOptions | None = None) -> "Agent":
        base_options = options or AgentOptions()
        return cls(replace(base_options, session=deepcopy(session)))

    @classmethod
    def from_checkpoint(cls, checkpoint: Checkpoint, options: AgentOptions | None = None) -> "Agent":
        cls._validate_checkpoint(checkpoint)
        return cls.from_session(checkpoint.session, options)

    @property
    def state(self) -> AgentStateView:
        return self._state.snapshot()

    @property
    def session_id(self) -> str | None:
        return self._session_id

    def subscribe(self, listener: Callable[[AgentEvent, CancelToken | None], Awaitable[None] | None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def _unsubscribe() -> None:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

        return _unsubscribe

    def steer(self, message: AgentMessage) -> None:
        self._steering_queue.enqueue(message)

    def follow_up(self, message: AgentMessage) -> None:
        self._follow_up_queue.enqueue(message)

    def abort(self) -> None:
        if self._active_run is not None:
            self._active_run.cancel()

    async def wait_for_idle(self) -> None:
        if self._active_run is None:
            return
        await self._active_run.wait_idle()

    def reset(self) -> None:
        self._state.messages = []
        self._state.reset_runtime_fields()
        self._steering_queue.clear()
        self._follow_up_queue.clear()
        self._event_log = []
        self._event_seq = 0
        self._last_run_id = None
        self._completed_turn_index = 0
        self._current_turn_index = 0
        self._current_turn_id = None
        self._open_message_ids.clear()
        self._current_assistant_message_id = None
        self._stable_boundary = StableBoundary(kind="initialized", message_count=0)

    def export_session(self) -> AgentSession:
        self._ensure_exportable("export session")
        return self._snapshot_session()

    def export_checkpoint(self) -> Checkpoint:
        session = self.export_session()
        return Checkpoint(session=session, stable_boundary=deepcopy(session.stable_boundary))

    def replay_run(self, run_id: str | None = None) -> list[RecordedEvent]:
        return replay_events(self._event_log, run_id)

    async def prompt(self, input_value: str | AgentMessage | list[AgentMessage], images: list[ImageContent] | None = None) -> None:
        if self._active_run is not None:
            raise AgentAlreadyRunningError(
                "Agent is already processing a prompt. Use steer() or follow_up() to queue messages, or wait for completion."
            )
        prompt_messages = self._normalize_prompt_input(input_value, images)
        await self._run_prompt_messages(prompt_messages)

    async def continue_(self) -> None:
        if self._active_run is not None:
            raise AgentAlreadyRunningError("Agent is already processing. Wait for completion before continuing.")

        last_message = self._state.messages[-1] if self._state.messages else None
        if last_message is None:
            raise InvalidContinuationError("no messages to continue from")

        if last_message.role == "assistant":
            steering = self._steering_queue.drain()
            if steering:
                await self._run_prompt_messages(steering, skip_initial_steering_poll=True)
                return

            followups = self._follow_up_queue.drain()
            if followups:
                await self._run_prompt_messages(followups, skip_initial_steering_poll=False)
                return

            raise InvalidContinuationError("cannot continue from assistant")

        await self._run_continuation()

    def _normalize_prompt_input(
        self,
        input_value: str | AgentMessage | list[AgentMessage],
        images: list[ImageContent] | None = None,
    ) -> list[AgentMessage]:
        if isinstance(input_value, list):
            return list(input_value)

        if not isinstance(input_value, str):
            return [input_value]

        content: list[TextContent | ImageContent] = [TextContent(text=input_value)]
        if images:
            content.extend(images)
        return [UserMessage(content=content)]

    def _create_context_snapshot(self) -> AgentContext:
        return AgentContext(
            system_prompt=self._state.system_prompt,
            messages=list(self._state.messages),
            tools=list(self._state.tools),
        )

    def _create_loop_config(self, *, skip_initial_steering_poll: bool = False) -> AgentLoopConfig:
        first_steering_poll = skip_initial_steering_poll

        async def get_steering_messages() -> list[AgentMessage]:
            nonlocal first_steering_poll
            if first_steering_poll:
                first_steering_poll = False
                return []
            return self._steering_queue.drain()

        async def get_followup_messages() -> list[AgentMessage]:
            return self._follow_up_queue.drain()

        return AgentLoopConfig(
            model=self._state.model,
            stream_fn=self.stream_fn,
            convert_to_llm=self.convert_to_llm,
            transform_context=self.transform_context,
            get_steering_messages=get_steering_messages,
            get_followup_messages=get_followup_messages,
            tool_execution=self.tool_execution,
            before_tool_call=self.before_tool_call,
            after_tool_call=self.after_tool_call,
            thinking_level=self._state.thinking_level,
            metadata=dict(self.metadata),
        )

    async def _run_prompt_messages(self, messages: list[AgentMessage], *, skip_initial_steering_poll: bool = False) -> None:
        async def _executor(cancel_token: CancelToken) -> None:
            await self._process_event(AgentStartEvent())
            await run_agent_loop(
                prompts=messages,
                context=self._create_context_snapshot(),
                config=self._create_loop_config(skip_initial_steering_poll=skip_initial_steering_poll),
                emit=self._process_event,
                cancel_token=cancel_token,
            )

        await self._run_with_lifecycle(_executor)

    async def _run_continuation(self) -> None:
        async def _executor(cancel_token: CancelToken) -> None:
            await self._process_event(AgentStartEvent())
            await run_agent_loop_continue(
                context=self._create_context_snapshot(),
                config=self._create_loop_config(),
                emit=self._process_event,
                cancel_token=cancel_token,
            )

        await self._run_with_lifecycle(_executor)

    async def _run_with_lifecycle(self, executor: Callable[[CancelToken], Awaitable[None]]) -> None:
        if self._active_run is not None:
            raise AgentAlreadyRunningError("Agent is already processing.")

        self._active_run = RunHandle.create()
        self._current_turn_index = self._completed_turn_index
        self._current_turn_id = None
        self._open_message_ids.clear()
        self._current_assistant_message_id = None
        self._state.is_streaming = True
        self._state.streaming_message = None
        self._state.error_message = None

        try:
            await executor(self._active_run.cancel_token)
        except Exception as exc:
            await self._handle_run_failure(exc, self._active_run.cancel_token.is_cancelled())
        finally:
            self._finish_run()

    async def _handle_run_failure(self, error: Exception, aborted: bool) -> None:
        failure_message = AssistantMessage(
            content=[TextContent(text="")],
            api=self._state.model.api,
            provider=self._state.model.provider,
            model=self._state.model.id,
            usage=TokenUsage(),
            stop_reason="aborted" if aborted else "error",
            error_message=str(error),
        )
        await self._process_event(MessageStartEvent(message=failure_message))
        await self._process_event(MessageEndEvent(message=failure_message))
        await self._process_event(TurnEndEvent(message=failure_message, tool_results=[]))
        await self._process_event(AgentEndEvent(messages=[failure_message]))

    def _finish_run(self) -> None:
        self._state.is_streaming = False
        self._state.streaming_message = None
        self._state.pending_tool_calls = set()
        self._current_turn_id = None
        self._open_message_ids.clear()
        self._current_assistant_message_id = None
        self._stable_boundary = self._build_stable_boundary()
        if self._active_run is not None:
            self._active_run.mark_idle()
        self._active_run = None

    async def _process_event(self, event: AgentEvent) -> None:
        self._prepare_event(event)

        if event.type == "turn_start":
            self._current_turn_index += 1

        if event.type == "message_start":
            self._state.streaming_message = event.message
        elif event.type == "message_update":
            self._state.streaming_message = event.message
        elif event.type == "message_end":
            self._state.streaming_message = None
            self._state.messages.append(event.message)
        elif event.type == "tool_execution_start":
            pending = set(self._state.pending_tool_calls)
            pending.add(event.tool_call_id)
            self._state.pending_tool_calls = pending
        elif event.type == "tool_execution_end":
            pending = set(self._state.pending_tool_calls)
            pending.discard(event.tool_call_id)
            self._state.pending_tool_calls = pending
        elif event.type == "turn_end":
            self._completed_turn_index = self._current_turn_index
            if isinstance(event.message, AssistantMessage) and event.message.error_message:
                self._state.error_message = event.message.error_message
        elif event.type == "agent_end":
            self._state.streaming_message = None

        self._record_event(event)

        if self._active_run is None:
            raise ListenerOutsideRunError("Agent listener invoked outside active run")
        for listener in list(self._listeners):
            await _maybe_await(listener(event, self._active_run.cancel_token))

    def _prepare_event(self, event: AgentEvent) -> None:
        next_seq = self._event_seq + 1
        run_id = self._active_run.run_id if self._active_run is not None else self._last_run_id
        turn_id = self._resolve_turn_id(event)
        enrich_event(event, run_id=run_id, turn_id=turn_id, seq=next_seq)

        if isinstance(event, MessageStartEvent):
            if event.message_id is None:
                event.message_id = uuid4().hex
            self._open_message_ids.append(event.message_id)
            if event.message.role == "assistant":
                self._current_assistant_message_id = event.message_id
        elif isinstance(event, MessageUpdateEvent):
            if event.message_id is None:
                event.message_id = self._current_assistant_message_id or self._peek_open_message_id()
        elif isinstance(event, MessageEndEvent):
            if event.message_id is None:
                event.message_id = self._pop_open_message_id()
            else:
                self._remove_open_message_id(event.message_id)
            if event.message.role == "assistant":
                self._current_assistant_message_id = event.message_id
        elif isinstance(event, (ToolExecutionStartEvent, ToolExecutionUpdateEvent, ToolExecutionEndEvent)):
            if event.assistant_message_id is None:
                event.assistant_message_id = self._current_assistant_message_id
        elif isinstance(event, TurnEndEvent):
            if event.message_id is None:
                event.message_id = self._current_assistant_message_id
        elif isinstance(event, AgentEndEvent):
            self._current_turn_id = None

    def _resolve_turn_id(self, event: AgentEvent) -> str | None:
        if isinstance(event, TurnStartEvent):
            if event.turn_id is None:
                event.turn_id = uuid4().hex
            self._current_turn_id = event.turn_id
            return event.turn_id

        if isinstance(
            event,
            MessageStartEvent | MessageUpdateEvent | MessageEndEvent | ToolExecutionStartEvent | ToolExecutionUpdateEvent | ToolExecutionEndEvent | TurnEndEvent,
        ):
            return event.turn_id or self._current_turn_id

        return event.turn_id

    def _peek_open_message_id(self) -> str | None:
        return self._open_message_ids[0] if self._open_message_ids else None

    def _pop_open_message_id(self) -> str | None:
        if not self._open_message_ids:
            return self._current_assistant_message_id
        return self._open_message_ids.popleft()

    def _remove_open_message_id(self, message_id: str) -> None:
        try:
            self._open_message_ids.remove(message_id)
        except ValueError:
            pass

    def _record_event(self, event: AgentEvent) -> None:
        if event.type == "agent_end":
            self._last_run_id = event.run_id
            self._current_turn_id = None
            self._current_assistant_message_id = None

        self._event_seq = event.seq
        self._event_log.append(
            RecordedEvent(
                event_id=event.event_id or uuid4().hex,
                run_id=event.run_id,
                turn_id=event.turn_id,
                seq=event.seq,
                timestamp=event.timestamp,
                turn_index=self._current_turn_index or None,
                type=event.type,
                payload=self._serialize_event_payload(event),
            )
        )
        if event.type == "turn_end":
            self._current_assistant_message_id = None

    def _serialize_event_payload(self, event: AgentEvent) -> dict[str, Any]:
        if isinstance(event, (MessageStartEvent, MessageEndEvent)):
            return {
                "message_id": event.message_id,
                "message": serialize_message(event.message),
            }
        if isinstance(event, MessageUpdateEvent):
            return {
                "message_id": event.message_id,
                "message": serialize_message(event.message),
                "assistant_message_event_type": event.assistant_message_event.type,
            }
        if isinstance(event, ToolExecutionStartEvent):
            return {
                "assistant_message_id": event.assistant_message_id,
                "tool_call_id": event.tool_call_id,
                "tool_name": event.tool_name,
                "args": deepcopy(event.args),
            }
        if isinstance(event, ToolExecutionUpdateEvent):
            return {
                "assistant_message_id": event.assistant_message_id,
                "tool_call_id": event.tool_call_id,
                "tool_name": event.tool_name,
                "args": deepcopy(event.args),
                "partial_result": serialize_tool_result(event.partial_result),
            }
        if isinstance(event, ToolExecutionEndEvent):
            return {
                "assistant_message_id": event.assistant_message_id,
                "tool_call_id": event.tool_call_id,
                "tool_name": event.tool_name,
                "is_error": event.is_error,
                "result": serialize_tool_result(event.result),
            }
        if isinstance(event, TurnEndEvent):
            return {
                "message_id": event.message_id,
                "message": serialize_message(event.message),
                "tool_results": [serialize_message(message) for message in event.tool_results],
            }
        if isinstance(event, AgentEndEvent):
            return {"messages": [serialize_message(message) for message in event.messages]}
        return {}

    def _ensure_exportable(self, operation: str) -> None:
        if self._active_run is not None or self._state.is_streaming or self._state.streaming_message is not None:
            raise SessionExportError(f"Cannot {operation} while the agent is mid-run")
        if self._state.pending_tool_calls:
            raise SessionExportError(f"Cannot {operation} with pending tool executions")

    def _snapshot_session(self) -> AgentSession:
        return AgentSession(
            session_id=self._session_id,
            system_prompt=self._state.system_prompt,
            model=deepcopy(self._state.model),
            thinking_level=self._state.thinking_level,
            messages=deepcopy(self._state.messages),
            tool_refs=build_tool_references(self._state.tools),
            steering_queue=QueueSnapshot(mode=self._steering_queue.mode, messages=deepcopy(self._steering_queue.snapshot())),
            follow_up_queue=QueueSnapshot(mode=self._follow_up_queue.mode, messages=deepcopy(self._follow_up_queue.snapshot())),
            metadata=deepcopy(self.metadata),
            stable_boundary=deepcopy(self._stable_boundary),
            event_log=deepcopy(self._event_log),
        )

    def _build_stable_boundary(self) -> StableBoundary:
        kind = "initialized" if not self._state.messages else "idle"
        return StableBoundary(
            kind=kind,
            message_count=len(self._state.messages),
            event_seq=self._event_seq,
            last_run_id=self._last_run_id,
            last_turn_index=self._completed_turn_index,
        )

    @staticmethod
    def _validate_checkpoint(checkpoint: Checkpoint) -> None:
        if checkpoint.schema_version != checkpoint.session.schema_version:
            raise CheckpointImportError("Checkpoint schema version does not match session schema version")
        Agent._validate_checkpoint_boundary_matches_session(checkpoint)
        Agent._validate_session_boundary_consistency(checkpoint.session)

    @staticmethod
    def _validate_checkpoint_boundary_matches_session(checkpoint: Checkpoint) -> None:
        checkpoint_boundary = checkpoint.stable_boundary
        session_boundary = checkpoint.session.stable_boundary
        for field_name in (
            "kind",
            "captured_at",
            "message_count",
            "event_seq",
            "last_run_id",
            "last_turn_index",
        ):
            checkpoint_value = getattr(checkpoint_boundary, field_name)
            session_value = getattr(session_boundary, field_name)
            if checkpoint_value != session_value:
                raise CheckpointImportError(
                    f"Checkpoint stable_boundary.{field_name} does not match session stable_boundary"
                )

    @staticmethod
    def _validate_session_boundary_consistency(session: AgentSession) -> None:
        boundary = session.stable_boundary
        if boundary.kind not in {"initialized", "idle"}:
            raise CheckpointImportError("Session stable boundary is not resumable")

        expected_kind = "initialized" if not session.messages else "idle"
        if boundary.kind != expected_kind:
            raise CheckpointImportError("Session stable_boundary.kind does not match session transcript")
        if boundary.message_count != len(session.messages):
            raise CheckpointImportError("Session stable_boundary.message_count does not match session transcript")

        event_seq = max((event.seq for event in session.event_log), default=0)
        if boundary.event_seq != event_seq:
            raise CheckpointImportError("Session stable_boundary.event_seq does not match event log")

        last_run_id = next((event.run_id for event in reversed(session.event_log) if event.run_id is not None), None)
        if boundary.last_run_id != last_run_id:
            raise CheckpointImportError("Session stable_boundary.last_run_id does not match event log")

        last_turn_index = max((event.turn_index or 0 for event in session.event_log), default=0)
        if boundary.last_turn_index != last_turn_index:
            raise CheckpointImportError("Session stable_boundary.last_turn_index does not match event log")


__all__ = [
    "Agent",
]
