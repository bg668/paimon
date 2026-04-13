from __future__ import annotations

from typing import Iterable

from .config import MessageQueueMode
from .models import AgentMessage


class PendingMessageQueue:
    __slots__ = ("_messages", "_mode")

    def __init__(
        self,
        mode: MessageQueueMode = "one-at-a-time",
        messages: Iterable[AgentMessage] | None = None,
    ) -> None:
        self._messages = list(messages or [])
        self.mode = mode

    @property
    def mode(self) -> MessageQueueMode:
        return self._mode

    @mode.setter
    def mode(self, value: MessageQueueMode) -> None:
        if value not in ("all", "one-at-a-time"):
            raise ValueError(f"Unsupported queue mode: {value}")
        self._mode = value

    def enqueue(self, message: AgentMessage) -> None:
        self._messages.append(message)

    def has_items(self) -> bool:
        return bool(self._messages)

    def snapshot(self) -> list[AgentMessage]:
        return list(self._messages)

    def drain(self) -> list[AgentMessage]:
        if self.mode == "all":
            drained = self._messages[:]
            self._messages.clear()
            return drained

        first = self._messages[0] if self._messages else None
        if first is None:
            return []
        self._messages = self._messages[1:]
        return [first]

    def clear(self) -> None:
        self._messages.clear()

    def __len__(self) -> int:
        return len(self._messages)


__all__ = [
    "PendingMessageQueue",
]
