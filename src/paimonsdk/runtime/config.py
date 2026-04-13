from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Literal, Sequence, TypeAlias

from .models import (
    AgentContext,
    AgentMessage,
    AgentTool,
    AgentToolResult,
    AssistantMessage,
    LLMMessage,
    ModelInfo,
    StreamFn,
    ThinkingLevel,
    ToolCallContent,
    ToolError,
    ToolExecutionMode,
    ToolResultStatus,
    ToolResultContent,
)

if TYPE_CHECKING:
    from .run_control import CancelToken
    from .session import AgentSession


MessageQueueMode: TypeAlias = Literal["all", "one-at-a-time"]
ConvertToLLM: TypeAlias = Callable[[Sequence[AgentMessage]], Awaitable[Sequence[LLMMessage]] | Sequence[LLMMessage]]
TransformContextHook: TypeAlias = Callable[
    [Sequence[AgentMessage], "CancelToken | None"],
    Awaitable[Sequence[AgentMessage]] | Sequence[AgentMessage],
]
MessageSupplier: TypeAlias = Callable[[], Awaitable[Sequence[AgentMessage]] | Sequence[AgentMessage]]
BeforeToolCallHook: TypeAlias = Callable[
    ["BeforeToolCallContext", "CancelToken | None"],
    Awaitable["BeforeToolCallResult | None"] | "BeforeToolCallResult | None",
]
AfterToolCallHook: TypeAlias = Callable[
    ["AfterToolCallContext", "CancelToken | None"],
    Awaitable["AfterToolCallResult | None"] | "AfterToolCallResult | None",
]


class _UnsetValue:
    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


UNSET = _UnsetValue()
UnsetValue: TypeAlias = _UnsetValue


@dataclass(slots=True)
class BeforeToolCallContext:
    assistant_message: AssistantMessage
    tool_call: ToolCallContent
    tool: AgentTool[Any, Any]
    args: Any
    context: AgentContext


@dataclass(slots=True)
class BeforeToolCallResult:
    block: bool = False
    reason: str | None = None
    result: AgentToolResult | None = None


@dataclass(slots=True)
class AfterToolCallContext:
    assistant_message: AssistantMessage
    tool_call: ToolCallContent
    tool: AgentTool[Any, Any]
    args: Any
    result: AgentToolResult
    status: ToolResultStatus
    error: ToolError | None
    context: AgentContext


@dataclass(slots=True)
class AfterToolCallResult:
    result: AgentToolResult | UnsetValue = UNSET


@dataclass(slots=True)
class AgentOptions:
    system_prompt: str = ""
    model: ModelInfo = field(default_factory=ModelInfo)
    thinking_level: ThinkingLevel = ThinkingLevel.OFF
    tools: list[AgentTool[Any, Any]] = field(default_factory=list)
    messages: list[AgentMessage] = field(default_factory=list)
    session: "AgentSession | None" = None
    convert_to_llm: ConvertToLLM | None = None
    transform_context: TransformContextHook | None = None
    stream_fn: StreamFn | None = None
    before_tool_call: BeforeToolCallHook | None = None
    after_tool_call: AfterToolCallHook | None = None
    steering_mode: MessageQueueMode = "one-at-a-time"
    follow_up_mode: MessageQueueMode = "one-at-a-time"
    tool_execution: ToolExecutionMode = ToolExecutionMode.PARALLEL
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentLoopConfig:
    model: ModelInfo
    stream_fn: StreamFn
    convert_to_llm: ConvertToLLM
    transform_context: TransformContextHook | None = None
    get_steering_messages: MessageSupplier | None = None
    get_followup_messages: MessageSupplier | None = None
    tool_execution: ToolExecutionMode = ToolExecutionMode.PARALLEL
    before_tool_call: BeforeToolCallHook | None = None
    after_tool_call: AfterToolCallHook | None = None
    thinking_level: ThinkingLevel = ThinkingLevel.OFF
    metadata: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "AfterToolCallContext",
    "AfterToolCallHook",
    "AfterToolCallResult",
    "AgentLoopConfig",
    "AgentOptions",
    "BeforeToolCallContext",
    "BeforeToolCallHook",
    "BeforeToolCallResult",
    "ConvertToLLM",
    "MessageQueueMode",
    "MessageSupplier",
    "TransformContextHook",
    "UNSET",
    "UnsetValue",
]
