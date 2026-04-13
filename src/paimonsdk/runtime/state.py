from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .models import AgentMessage, AgentTool, ModelInfo, ThinkingLevel


class MutableAgentState:
    __slots__ = (
        "system_prompt",
        "model",
        "thinking_level",
        "_tools",
        "_messages",
        "is_streaming",
        "streaming_message",
        "_pending_tool_calls",
        "error_message",
    )

    def __init__(
        self,
        *,
        system_prompt: str = "",
        model: ModelInfo | None = None,
        thinking_level: ThinkingLevel = ThinkingLevel.OFF,
        tools: list[AgentTool[Any, Any]] | None = None,
        messages: list[AgentMessage] | None = None,
        is_streaming: bool = False,
        streaming_message: AgentMessage | None = None,
        pending_tool_calls: set[str] | None = None,
        error_message: str | None = None,
    ) -> None:
        self.system_prompt = system_prompt
        self.model = model if model is not None else ModelInfo()
        self.thinking_level = thinking_level
        self._tools = list(tools or [])
        self._messages = list(messages or [])
        self.is_streaming = is_streaming
        self.streaming_message = streaming_message
        self._pending_tool_calls = set(pending_tool_calls or set())
        self.error_message = error_message

    @property
    def tools(self) -> list[AgentTool[Any, Any]]:
        return self._tools

    @tools.setter
    def tools(self, value: list[AgentTool[Any, Any]]) -> None:
        self._tools = list(value)

    @property
    def messages(self) -> list[AgentMessage]:
        return self._messages

    @messages.setter
    def messages(self, value: list[AgentMessage]) -> None:
        self._messages = list(value)

    @property
    def pending_tool_calls(self) -> set[str]:
        return self._pending_tool_calls

    @pending_tool_calls.setter
    def pending_tool_calls(self, value: set[str]) -> None:
        self._pending_tool_calls = set(value)

    def reset_runtime_fields(self) -> None:
        self.is_streaming = False
        self.streaming_message = None
        self.pending_tool_calls = set()
        self.error_message = None

    def snapshot(self) -> "AgentStateView":
        return AgentStateView(self)


@dataclass(frozen=True, slots=True)
class AgentStateView:
    _state: MutableAgentState

    @property
    def system_prompt(self) -> str:
        return self._state.system_prompt

    @property
    def model(self) -> ModelInfo:
        return deepcopy(self._state.model)

    @property
    def thinking_level(self) -> ThinkingLevel:
        return self._state.thinking_level

    @property
    def tools(self) -> tuple[AgentTool[Any, Any], ...]:
        return tuple(self._state.tools)

    @property
    def messages(self) -> tuple[AgentMessage, ...]:
        return tuple(deepcopy(self._state.messages))

    @property
    def is_streaming(self) -> bool:
        return self._state.is_streaming

    @property
    def streaming_message(self) -> AgentMessage | None:
        if self._state.streaming_message is None:
            return None
        return deepcopy(self._state.streaming_message)

    @property
    def pending_tool_calls(self) -> frozenset[str]:
        return frozenset(self._state.pending_tool_calls)

    @property
    def error_message(self) -> str | None:
        return self._state.error_message


__all__ = [
    "AgentStateView",
    "MutableAgentState",
]
