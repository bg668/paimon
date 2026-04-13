from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from time import time
from typing import TYPE_CHECKING, Any, AsyncIterator, Awaitable, Callable, Literal, Mapping, Protocol, TypeAlias, TypeVar

if TYPE_CHECKING:
    from .config import AgentLoopConfig
    from .run_control import CancelToken


def utc_timestamp_ms() -> int:
    return int(time() * 1000)


class ThinkingLevel(str, Enum):
    OFF = "off"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"


class ToolExecutionMode(str, Enum):
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"


class ToolResultStatus(str, Enum):
    OK = "ok"
    ERROR = "error"
    BLOCKED = "blocked"


MessageRole: TypeAlias = Literal["user", "assistant", "toolResult"]
AssistantStopReason: TypeAlias = Literal[
    "stop",
    "tool_calls",
    "length",
    "content_filter",
    "error",
    "aborted",
    "unknown",
]
AssistantStreamEventType: TypeAlias = Literal[
    "start",
    "text_delta",
    "thinking_delta",
    "tool_call_delta",
    "done",
    "error",
]


@dataclass(slots=True)
class UsageCost:
    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0
    total: float = 0.0


@dataclass(slots=True)
class TokenUsage:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    total_tokens: int = 0
    cost: UsageCost = field(default_factory=UsageCost)


@dataclass(slots=True)
class ModelPricing:
    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0


@dataclass(slots=True)
class ModelInfo:
    id: str = "unknown"
    name: str = "unknown"
    api: str = "unknown"
    provider: str = "unknown"
    base_url: str = ""
    reasoning: bool = False
    input_modalities: tuple[str, ...] = ()
    cost: ModelPricing = field(default_factory=ModelPricing)
    context_window: int = 0
    max_tokens: int = 0


@dataclass(slots=True)
class TextContent:
    type: Literal["text"] = "text"
    text: str = ""


@dataclass(slots=True)
class ImageContent:
    type: Literal["image"] = "image"
    image_url: str | None = None
    mime_type: str | None = None
    detail: str | None = None
    alt_text: str | None = None


@dataclass(slots=True)
class ThinkingContent:
    type: Literal["thinking"] = "thinking"
    thinking: str = ""
    signature: str | None = None


@dataclass(slots=True)
class ToolCallContent:
    type: Literal["toolCall"] = "toolCall"
    id: str = ""
    name: str = ""
    arguments: Any = field(default_factory=dict)


UserContent: TypeAlias = TextContent | ImageContent
AssistantContent: TypeAlias = TextContent | ImageContent | ThinkingContent | ToolCallContent
ToolResultContent: TypeAlias = TextContent | ImageContent
ContentBlock: TypeAlias = UserContent | AssistantContent


@dataclass(slots=True)
class ToolArtifactRef:
    artifact_id: str = ""
    kind: str = "generic"
    uri: str | None = None
    name: str | None = None
    mime_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolError:
    code: str = "tool_error"
    message: str = ""
    retryable: bool = False
    details: Any = None


@dataclass(slots=True, kw_only=True)
class UserMessage:
    role: Literal["user"] = "user"
    content: list[UserContent] = field(default_factory=list)
    timestamp: int = field(default_factory=utc_timestamp_ms)


@dataclass(slots=True, kw_only=True)
class AssistantMessage:
    role: Literal["assistant"] = "assistant"
    content: list[AssistantContent] = field(default_factory=list)
    stop_reason: AssistantStopReason = "stop"
    error_message: str | None = None
    usage: TokenUsage = field(default_factory=TokenUsage)
    provider: str = "unknown"
    model: str = "unknown"
    api: str = "unknown"
    timestamp: int = field(default_factory=utc_timestamp_ms)


@dataclass(slots=True, kw_only=True)
class ToolResultMessage:
    role: Literal["toolResult"] = "toolResult"
    tool_call_id: str = ""
    tool_name: str = ""
    content: list[ToolResultContent] = field(default_factory=list)
    details: Any = None
    artifacts: list[ToolArtifactRef] = field(default_factory=list)
    error: ToolError | None = None
    status: ToolResultStatus = ToolResultStatus.OK
    is_error: bool = False
    timestamp: int = field(default_factory=utc_timestamp_ms)


AgentMessage: TypeAlias = UserMessage | AssistantMessage | ToolResultMessage
LLMMessage: TypeAlias = UserMessage | AssistantMessage | ToolResultMessage


@dataclass(slots=True)
class AgentToolResult:
    content: list[ToolResultContent] = field(default_factory=list)
    details: Any = None
    artifacts: list[ToolArtifactRef] = field(default_factory=list)
    error: ToolError | None = None
    status: ToolResultStatus = ToolResultStatus.OK
    metadata: dict[str, Any] = field(default_factory=dict)


TToolDetails = TypeVar("TToolDetails")
TToolArgs = TypeVar("TToolArgs")

AgentToolUpdateCallback: TypeAlias = Callable[[AgentToolResult], None]


class AgentTool(Protocol[TToolArgs, TToolDetails]):
    name: str
    label: str
    description: str | None
    input_schema: Mapping[str, Any] | None
    prepare_arguments: Callable[[Any], TToolArgs] | None

    async def execute(
        self,
        tool_call_id: str,
        params: TToolArgs,
        cancel_token: CancelToken | None = None,
        on_update: AgentToolUpdateCallback | None = None,
    ) -> AgentToolResult: ...


@dataclass(slots=True)
class AgentContext:
    system_prompt: str = ""
    messages: list[AgentMessage] = field(default_factory=list)
    tools: list[AgentTool[Any, Any]] = field(default_factory=list)


@dataclass(slots=True)
class AssistantStreamStart:
    type: Literal["start"] = "start"
    partial: AssistantMessage = field(default_factory=AssistantMessage)


@dataclass(slots=True)
class AssistantTextDelta:
    type: Literal["text_delta"] = "text_delta"
    partial: AssistantMessage = field(default_factory=AssistantMessage)
    delta: str = ""
    index: int = 0


@dataclass(slots=True)
class AssistantThinkingDelta:
    type: Literal["thinking_delta"] = "thinking_delta"
    partial: AssistantMessage = field(default_factory=AssistantMessage)
    delta: str = ""
    index: int = 0


@dataclass(slots=True)
class AssistantToolCallDelta:
    type: Literal["tool_call_delta"] = "tool_call_delta"
    partial: AssistantMessage = field(default_factory=AssistantMessage)
    tool_call: ToolCallContent = field(default_factory=ToolCallContent)
    index: int = 0
    arguments_delta: str = ""


@dataclass(slots=True)
class AssistantStreamDone:
    type: Literal["done"] = "done"
    partial: AssistantMessage = field(default_factory=AssistantMessage)


@dataclass(slots=True)
class AssistantStreamError:
    type: Literal["error"] = "error"
    partial: AssistantMessage = field(default_factory=AssistantMessage)
    error_message: str | None = None


AssistantMessageEvent: TypeAlias = (
    AssistantStreamStart
    | AssistantTextDelta
    | AssistantThinkingDelta
    | AssistantToolCallDelta
    | AssistantStreamDone
    | AssistantStreamError
)


class AssistantMessageEventStream(Protocol):
    def __aiter__(self) -> AsyncIterator[AssistantMessageEvent]: ...

    async def result(self) -> AssistantMessage: ...


StreamFn: TypeAlias = Callable[
    [ModelInfo, AgentContext, "AgentLoopConfig", "CancelToken | None"],
    Awaitable[AssistantMessageEventStream] | AssistantMessageEventStream,
]


__all__ = [
    "AgentContext",
    "AgentMessage",
    "AgentTool",
    "AgentToolResult",
    "AgentToolUpdateCallback",
    "AssistantContent",
    "AssistantMessage",
    "AssistantMessageEvent",
    "AssistantMessageEventStream",
    "AssistantStopReason",
    "AssistantStreamDone",
    "AssistantStreamError",
    "AssistantStreamEventType",
    "AssistantStreamStart",
    "AssistantTextDelta",
    "AssistantThinkingDelta",
    "AssistantToolCallDelta",
    "ContentBlock",
    "ImageContent",
    "LLMMessage",
    "MessageRole",
    "ModelInfo",
    "ModelPricing",
    "StreamFn",
    "TextContent",
    "ThinkingContent",
    "ThinkingLevel",
    "TokenUsage",
    "ToolArtifactRef",
    "ToolCallContent",
    "ToolError",
    "ToolExecutionMode",
    "ToolResultStatus",
    "ToolResultContent",
    "ToolResultMessage",
    "UsageCost",
    "UserContent",
    "UserMessage",
    "utc_timestamp_ms",
]
