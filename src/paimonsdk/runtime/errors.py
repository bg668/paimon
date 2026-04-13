from __future__ import annotations


class AgentRuntimeError(RuntimeError):
    """Base runtime error for the agent SDK."""


class AgentAlreadyRunningError(AgentRuntimeError):
    """Raised when a prompt or continuation is started while a run is active."""


class InvalidContinuationError(AgentRuntimeError):
    """Raised when continue_() is called from an invalid transcript boundary."""


class ListenerOutsideRunError(AgentRuntimeError):
    """Raised when an event listener is awaited without an active run context."""


class ToolPreparationError(AgentRuntimeError):
    """Raised when a tool call cannot be prepared for execution."""


class OpenAIAdapterError(AgentRuntimeError):
    """Raised when OpenAI chat.completions mapping or transport fails."""


class SessionExportError(AgentRuntimeError):
    """Raised when the current runtime state cannot be exported as a stable session."""


class CheckpointImportError(AgentRuntimeError):
    """Raised when a session or checkpoint payload cannot be restored safely."""


__all__ = [
    "AgentAlreadyRunningError",
    "AgentRuntimeError",
    "CheckpointImportError",
    "InvalidContinuationError",
    "ListenerOutsideRunError",
    "OpenAIAdapterError",
    "SessionExportError",
    "ToolPreparationError",
]
