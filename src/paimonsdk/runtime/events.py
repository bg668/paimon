from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Literal, Sequence, TypeAlias
from uuid import uuid4

from .models import AgentMessage, AgentToolResult, AssistantMessage, AssistantMessageEvent, ToolResultMessage, utc_timestamp_ms

if TYPE_CHECKING:
    from .run_control import CancelToken


@dataclass(slots=True, kw_only=True)
class EventEnvelope:
    event_id: str | None = None
    run_id: str | None = None
    turn_id: str | None = None
    seq: int = 0
    timestamp: int = 0


@dataclass(slots=True)
class AgentStartEvent(EventEnvelope):
    type: Literal["agent_start"] = field(init=False, default="agent_start")


@dataclass(slots=True)
class AgentEndEvent(EventEnvelope):
    messages: Sequence[AgentMessage]
    type: Literal["agent_end"] = field(init=False, default="agent_end")


@dataclass(slots=True)
class TurnStartEvent(EventEnvelope):
    type: Literal["turn_start"] = field(init=False, default="turn_start")


@dataclass(slots=True)
class TurnEndEvent(EventEnvelope):
    message: AgentMessage
    tool_results: Sequence[ToolResultMessage]
    message_id: str | None = None
    type: Literal["turn_end"] = field(init=False, default="turn_end")


@dataclass(slots=True)
class MessageStartEvent(EventEnvelope):
    message: AgentMessage
    message_id: str | None = None
    type: Literal["message_start"] = field(init=False, default="message_start")


@dataclass(slots=True)
class MessageUpdateEvent(EventEnvelope):
    message: AssistantMessage
    assistant_message_event: AssistantMessageEvent
    message_id: str | None = None
    type: Literal["message_update"] = field(init=False, default="message_update")


@dataclass(slots=True)
class MessageEndEvent(EventEnvelope):
    message: AgentMessage
    message_id: str | None = None
    type: Literal["message_end"] = field(init=False, default="message_end")


@dataclass(slots=True)
class ToolExecutionStartEvent(EventEnvelope):
    tool_call_id: str
    tool_name: str
    args: Any
    assistant_message_id: str | None = None
    type: Literal["tool_execution_start"] = field(init=False, default="tool_execution_start")


@dataclass(slots=True)
class ToolExecutionUpdateEvent(EventEnvelope):
    tool_call_id: str
    tool_name: str
    args: Any
    partial_result: AgentToolResult
    assistant_message_id: str | None = None
    type: Literal["tool_execution_update"] = field(init=False, default="tool_execution_update")


@dataclass(slots=True)
class ToolExecutionEndEvent(EventEnvelope):
    tool_call_id: str
    tool_name: str
    result: AgentToolResult
    is_error: bool
    assistant_message_id: str | None = None
    type: Literal["tool_execution_end"] = field(init=False, default="tool_execution_end")


AgentEvent: TypeAlias = (
    AgentStartEvent
    | AgentEndEvent
    | TurnStartEvent
    | TurnEndEvent
    | MessageStartEvent
    | MessageUpdateEvent
    | MessageEndEvent
    | ToolExecutionStartEvent
    | ToolExecutionUpdateEvent
    | ToolExecutionEndEvent
)
AgentEventListener: TypeAlias = Callable[[AgentEvent, "CancelToken | None"], Awaitable[None] | None]


def enrich_event(
    event: AgentEvent,
    *,
    run_id: str | None = None,
    turn_id: str | None = None,
    seq: int | None = None,
    timestamp: int | None = None,
) -> AgentEvent:
    if event.event_id is None:
        event.event_id = uuid4().hex
    if event.run_id is None and run_id is not None:
        event.run_id = run_id
    if event.turn_id is None and turn_id is not None:
        event.turn_id = turn_id
    if not event.seq and seq is not None:
        event.seq = seq
    if not event.timestamp:
        event.timestamp = timestamp if timestamp is not None else utc_timestamp_ms()
    return event


__all__ = [
    "AgentEndEvent",
    "AgentEvent",
    "AgentEventListener",
    "AgentStartEvent",
    "EventEnvelope",
    "MessageEndEvent",
    "MessageStartEvent",
    "MessageUpdateEvent",
    "ToolExecutionEndEvent",
    "ToolExecutionStartEvent",
    "ToolExecutionUpdateEvent",
    "TurnEndEvent",
    "TurnStartEvent",
    "enrich_event",
]
