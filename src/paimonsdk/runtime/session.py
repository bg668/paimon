from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Sequence
from uuid import uuid4

from .config import MessageQueueMode
from .models import (
    AgentMessage,
    AgentTool,
    AgentToolResult,
    AssistantMessage,
    ImageContent,
    ModelInfo,
    ModelPricing,
    TextContent,
    ThinkingContent,
    ThinkingLevel,
    TokenUsage,
    ToolArtifactRef,
    ToolCallContent,
    ToolError,
    ToolResultStatus,
    ToolResultMessage,
    UsageCost,
    UserMessage,
    utc_timestamp_ms,
)


SESSION_SCHEMA_VERSION = 1
StableBoundaryKind = Literal["initialized", "idle"]


def _serialize_cost(cost: UsageCost) -> dict[str, Any]:
    return {
        "input": cost.input,
        "output": cost.output,
        "cache_read": cost.cache_read,
        "cache_write": cost.cache_write,
        "total": cost.total,
    }


def _deserialize_cost(data: Mapping[str, Any]) -> UsageCost:
    return UsageCost(
        input=float(data.get("input", 0.0)),
        output=float(data.get("output", 0.0)),
        cache_read=float(data.get("cache_read", 0.0)),
        cache_write=float(data.get("cache_write", 0.0)),
        total=float(data.get("total", 0.0)),
    )


def _serialize_usage(usage: TokenUsage) -> dict[str, Any]:
    return {
        "input": usage.input,
        "output": usage.output,
        "cache_read": usage.cache_read,
        "cache_write": usage.cache_write,
        "total_tokens": usage.total_tokens,
        "cost": _serialize_cost(usage.cost),
    }


def _deserialize_usage(data: Mapping[str, Any]) -> TokenUsage:
    return TokenUsage(
        input=int(data.get("input", 0)),
        output=int(data.get("output", 0)),
        cache_read=int(data.get("cache_read", 0)),
        cache_write=int(data.get("cache_write", 0)),
        total_tokens=int(data.get("total_tokens", 0)),
        cost=_deserialize_cost(data.get("cost", {})),
    )


def serialize_model_info(model: ModelInfo) -> dict[str, Any]:
    return {
        "id": model.id,
        "name": model.name,
        "api": model.api,
        "provider": model.provider,
        "base_url": model.base_url,
        "reasoning": model.reasoning,
        "input_modalities": list(model.input_modalities),
        "cost": {
            "input": model.cost.input,
            "output": model.cost.output,
            "cache_read": model.cost.cache_read,
            "cache_write": model.cost.cache_write,
        },
        "context_window": model.context_window,
        "max_tokens": model.max_tokens,
    }


def deserialize_model_info(data: Mapping[str, Any]) -> ModelInfo:
    cost_data = data.get("cost", {})
    return ModelInfo(
        id=str(data.get("id", "unknown")),
        name=str(data.get("name", "unknown")),
        api=str(data.get("api", "unknown")),
        provider=str(data.get("provider", "unknown")),
        base_url=str(data.get("base_url", "")),
        reasoning=bool(data.get("reasoning", False)),
        input_modalities=tuple(data.get("input_modalities", ())),
        cost=ModelPricing(
            input=float(cost_data.get("input", 0.0)),
            output=float(cost_data.get("output", 0.0)),
            cache_read=float(cost_data.get("cache_read", 0.0)),
            cache_write=float(cost_data.get("cache_write", 0.0)),
        ),
        context_window=int(data.get("context_window", 0)),
        max_tokens=int(data.get("max_tokens", 0)),
    )


def serialize_content_block(block: Any) -> dict[str, Any]:
    if isinstance(block, TextContent):
        return {"type": "text", "text": block.text}
    if isinstance(block, ImageContent):
        return {
            "type": "image",
            "image_url": block.image_url,
            "mime_type": block.mime_type,
            "detail": block.detail,
            "alt_text": block.alt_text,
        }
    if isinstance(block, ThinkingContent):
        return {
            "type": "thinking",
            "thinking": block.thinking,
            "signature": block.signature,
        }
    if isinstance(block, ToolCallContent):
        return {
            "type": "toolCall",
            "id": block.id,
            "name": block.name,
            "arguments": deepcopy(block.arguments),
        }
    raise TypeError(f"Unsupported content block: {type(block)!r}")


def deserialize_content_block(data: Mapping[str, Any]) -> TextContent | ImageContent | ThinkingContent | ToolCallContent:
    block_type = data.get("type")
    if block_type == "text":
        return TextContent(text=str(data.get("text", "")))
    if block_type == "image":
        return ImageContent(
            image_url=data.get("image_url"),
            mime_type=data.get("mime_type"),
            detail=data.get("detail"),
            alt_text=data.get("alt_text"),
        )
    if block_type == "thinking":
        return ThinkingContent(
            thinking=str(data.get("thinking", "")),
            signature=data.get("signature"),
        )
    if block_type == "toolCall":
        return ToolCallContent(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            arguments=deepcopy(data.get("arguments", {})),
        )
    raise ValueError(f"Unsupported content block type: {block_type!r}")


def serialize_message(message: AgentMessage) -> dict[str, Any]:
    if isinstance(message, UserMessage):
        return {
            "role": "user",
            "content": [serialize_content_block(item) for item in message.content],
            "timestamp": message.timestamp,
        }
    if isinstance(message, AssistantMessage):
        return {
            "role": "assistant",
            "content": [serialize_content_block(item) for item in message.content],
            "stop_reason": message.stop_reason,
            "error_message": message.error_message,
            "usage": _serialize_usage(message.usage),
            "provider": message.provider,
            "model": message.model,
            "api": message.api,
            "timestamp": message.timestamp,
        }
    if isinstance(message, ToolResultMessage):
        return {
            "role": "toolResult",
            "tool_call_id": message.tool_call_id,
            "tool_name": message.tool_name,
            "content": [serialize_content_block(item) for item in message.content],
            "details": deepcopy(message.details),
            "artifacts": [serialize_artifact_ref(item) for item in message.artifacts],
            "error": None if message.error is None else serialize_tool_error(message.error),
            "status": message.status.value,
            "is_error": message.is_error,
            "timestamp": message.timestamp,
        }
    raise TypeError(f"Unsupported agent message: {type(message)!r}")


def deserialize_message(data: Mapping[str, Any]) -> AgentMessage:
    role = data.get("role")
    if role == "user":
        return UserMessage(
            content=[deserialize_content_block(item) for item in data.get("content", [])],
            timestamp=int(data.get("timestamp", utc_timestamp_ms())),
        )
    if role == "assistant":
        return AssistantMessage(
            content=[deserialize_content_block(item) for item in data.get("content", [])],
            stop_reason=str(data.get("stop_reason", "stop")),
            error_message=data.get("error_message"),
            usage=_deserialize_usage(data.get("usage", {})),
            provider=str(data.get("provider", "unknown")),
            model=str(data.get("model", "unknown")),
            api=str(data.get("api", "unknown")),
            timestamp=int(data.get("timestamp", utc_timestamp_ms())),
        )
    if role == "toolResult":
        return ToolResultMessage(
            tool_call_id=str(data.get("tool_call_id", "")),
            tool_name=str(data.get("tool_name", "")),
            content=[deserialize_content_block(item) for item in data.get("content", [])],
            details=deepcopy(data.get("details")),
            artifacts=[deserialize_artifact_ref(item) for item in data.get("artifacts", [])],
            error=None if data.get("error") is None else deserialize_tool_error(data.get("error", {})),
            status=ToolResultStatus(str(data.get("status", ToolResultStatus.OK.value))),
            is_error=bool(data.get("is_error", False)),
            timestamp=int(data.get("timestamp", utc_timestamp_ms())),
        )
    raise ValueError(f"Unsupported message role: {role!r}")


def serialize_artifact_ref(artifact: ToolArtifactRef) -> dict[str, Any]:
    return {
        "artifact_id": artifact.artifact_id,
        "kind": artifact.kind,
        "uri": artifact.uri,
        "name": artifact.name,
        "mime_type": artifact.mime_type,
        "metadata": deepcopy(artifact.metadata),
    }


def deserialize_artifact_ref(data: Mapping[str, Any]) -> ToolArtifactRef:
    return ToolArtifactRef(
        artifact_id=str(data.get("artifact_id", "")),
        kind=str(data.get("kind", "generic")),
        uri=data.get("uri"),
        name=data.get("name"),
        mime_type=data.get("mime_type"),
        metadata=deepcopy(data.get("metadata", {})),
    )


def serialize_tool_error(error: ToolError) -> dict[str, Any]:
    return {
        "code": error.code,
        "message": error.message,
        "retryable": error.retryable,
        "details": deepcopy(error.details),
    }


def deserialize_tool_error(data: Mapping[str, Any]) -> ToolError:
    return ToolError(
        code=str(data.get("code", "tool_error")),
        message=str(data.get("message", "")),
        retryable=bool(data.get("retryable", False)),
        details=deepcopy(data.get("details")),
    )


def serialize_tool_result(result: AgentToolResult) -> dict[str, Any]:
    return {
        "content": [serialize_content_block(item) for item in result.content],
        "details": deepcopy(result.details),
        "artifacts": [serialize_artifact_ref(item) for item in result.artifacts],
        "error": None if result.error is None else serialize_tool_error(result.error),
        "status": result.status.value,
        "metadata": deepcopy(result.metadata),
    }


@dataclass(slots=True)
class ToolReference:
    name: str
    label: str
    description: str | None = None
    input_schema: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "input_schema": deepcopy(self.input_schema),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ToolReference":
        return cls(
            name=str(data.get("name", "")),
            label=str(data.get("label", "")),
            description=data.get("description"),
            input_schema=deepcopy(data.get("input_schema")),
        )


def tool_reference_from_tool(tool: AgentTool[Any, Any]) -> ToolReference:
    return ToolReference(
        name=tool.name,
        label=tool.label,
        description=getattr(tool, "description", None),
        input_schema=deepcopy(getattr(tool, "input_schema", None)),
    )


def build_tool_references(tools: Sequence[AgentTool[Any, Any]]) -> list[ToolReference]:
    return [tool_reference_from_tool(tool) for tool in tools]


@dataclass(slots=True)
class QueueSnapshot:
    mode: MessageQueueMode = "one-at-a-time"
    messages: list[AgentMessage] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "messages": [serialize_message(message) for message in self.messages],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "QueueSnapshot":
        return cls(
            mode=str(data.get("mode", "one-at-a-time")),
            messages=[deserialize_message(message) for message in data.get("messages", [])],
        )


@dataclass(slots=True)
class StableBoundary:
    kind: StableBoundaryKind = "initialized"
    captured_at: int = field(default_factory=utc_timestamp_ms)
    message_count: int = 0
    event_seq: int = 0
    last_run_id: str | None = None
    last_turn_index: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "captured_at": self.captured_at,
            "message_count": self.message_count,
            "event_seq": self.event_seq,
            "last_run_id": self.last_run_id,
            "last_turn_index": self.last_turn_index,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StableBoundary":
        return cls(
            kind=str(data.get("kind", "initialized")),
            captured_at=int(data.get("captured_at", utc_timestamp_ms())),
            message_count=int(data.get("message_count", 0)),
            event_seq=int(data.get("event_seq", 0)),
            last_run_id=data.get("last_run_id"),
            last_turn_index=int(data.get("last_turn_index", 0)),
        )


@dataclass(slots=True)
class RecordedEvent:
    event_id: str = field(default_factory=lambda: uuid4().hex)
    run_id: str | None = None
    turn_id: str | None = None
    seq: int = 0
    timestamp: int = field(default_factory=utc_timestamp_ms)
    turn_index: int | None = None
    type: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "run_id": self.run_id,
            "turn_id": self.turn_id,
            "seq": self.seq,
            "timestamp": self.timestamp,
            "turn_index": self.turn_index,
            "type": self.type,
            "payload": deepcopy(self.payload),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RecordedEvent":
        return cls(
            event_id=str(data.get("event_id", uuid4().hex)),
            run_id=data.get("run_id"),
            turn_id=data.get("turn_id"),
            seq=int(data.get("seq", 0)),
            timestamp=int(data.get("timestamp", utc_timestamp_ms())),
            turn_index=None if data.get("turn_index") is None else int(data.get("turn_index")),
            type=str(data.get("type", "")),
            payload=deepcopy(data.get("payload", {})),
        )


@dataclass(slots=True)
class AgentSession:
    schema_version: int = SESSION_SCHEMA_VERSION
    session_id: str | None = None
    system_prompt: str = ""
    model: ModelInfo = field(default_factory=ModelInfo)
    thinking_level: ThinkingLevel = ThinkingLevel.OFF
    messages: list[AgentMessage] = field(default_factory=list)
    tool_refs: list[ToolReference] = field(default_factory=list)
    steering_queue: QueueSnapshot = field(default_factory=QueueSnapshot)
    follow_up_queue: QueueSnapshot = field(default_factory=QueueSnapshot)
    metadata: dict[str, Any] = field(default_factory=dict)
    stable_boundary: StableBoundary = field(default_factory=StableBoundary)
    event_log: list[RecordedEvent] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "system_prompt": self.system_prompt,
            "model": serialize_model_info(self.model),
            "thinking_level": self.thinking_level.value,
            "messages": [serialize_message(message) for message in self.messages],
            "tool_refs": [tool_ref.to_dict() for tool_ref in self.tool_refs],
            "steering_queue": self.steering_queue.to_dict(),
            "follow_up_queue": self.follow_up_queue.to_dict(),
            "metadata": deepcopy(self.metadata),
            "stable_boundary": self.stable_boundary.to_dict(),
            "event_log": [event.to_dict() for event in self.event_log],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AgentSession":
        return cls(
            schema_version=int(data.get("schema_version", SESSION_SCHEMA_VERSION)),
            session_id=data.get("session_id"),
            system_prompt=str(data.get("system_prompt", "")),
            model=deserialize_model_info(data.get("model", {})),
            thinking_level=ThinkingLevel(str(data.get("thinking_level", ThinkingLevel.OFF.value))),
            messages=[deserialize_message(message) for message in data.get("messages", [])],
            tool_refs=[ToolReference.from_dict(item) for item in data.get("tool_refs", [])],
            steering_queue=QueueSnapshot.from_dict(data.get("steering_queue", {})),
            follow_up_queue=QueueSnapshot.from_dict(data.get("follow_up_queue", {})),
            metadata=deepcopy(data.get("metadata", {})),
            stable_boundary=StableBoundary.from_dict(data.get("stable_boundary", {})),
            event_log=[RecordedEvent.from_dict(item) for item in data.get("event_log", [])],
        )


@dataclass(slots=True)
class Checkpoint:
    schema_version: int = SESSION_SCHEMA_VERSION
    checkpoint_id: str = field(default_factory=lambda: uuid4().hex)
    created_at: int = field(default_factory=utc_timestamp_ms)
    session: AgentSession = field(default_factory=AgentSession)
    stable_boundary: StableBoundary = field(default_factory=StableBoundary)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "checkpoint_id": self.checkpoint_id,
            "created_at": self.created_at,
            "session": self.session.to_dict(),
            "stable_boundary": self.stable_boundary.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Checkpoint":
        return cls(
            schema_version=int(data.get("schema_version", SESSION_SCHEMA_VERSION)),
            checkpoint_id=str(data.get("checkpoint_id", uuid4().hex)),
            created_at=int(data.get("created_at", utc_timestamp_ms())),
            session=AgentSession.from_dict(data.get("session", {})),
            stable_boundary=StableBoundary.from_dict(data.get("stable_boundary", {})),
        )


def replay_events(event_log: Sequence[RecordedEvent], run_id: str | None = None) -> list[RecordedEvent]:
    if not event_log:
        return []

    if run_id is None:
        for event in reversed(event_log):
            if event.run_id is not None:
                run_id = event.run_id
                break

    if run_id is None:
        return [deepcopy(event) for event in event_log]
    return [deepcopy(event) for event in event_log if event.run_id == run_id]


__all__ = [
    "AgentSession",
    "Checkpoint",
    "QueueSnapshot",
    "RecordedEvent",
    "SESSION_SCHEMA_VERSION",
    "StableBoundary",
    "StableBoundaryKind",
    "ToolReference",
    "build_tool_references",
    "deserialize_message",
    "deserialize_model_info",
    "deserialize_tool_error",
    "replay_events",
    "serialize_artifact_ref",
    "serialize_content_block",
    "serialize_message",
    "serialize_model_info",
    "serialize_tool_error",
    "serialize_tool_result",
    "tool_reference_from_tool",
]
