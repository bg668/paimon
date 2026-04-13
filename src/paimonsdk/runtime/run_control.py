from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import uuid4


class CancelToken:
    __slots__ = ("_cancelled",)

    def __init__(self) -> None:
        self._cancelled = asyncio.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled():
            raise asyncio.CancelledError("Operation cancelled")

    async def wait_cancelled(self) -> None:
        await self._cancelled.wait()


@dataclass(slots=True)
class RunHandle:
    cancel_token: CancelToken
    idle_future: asyncio.Future[None]
    run_id: str = field(default_factory=lambda: uuid4().hex)

    @classmethod
    def create(cls, *, cancel_token: CancelToken | None = None, run_id: str | None = None) -> "RunHandle":
        loop = asyncio.get_running_loop()
        return cls(
            cancel_token=cancel_token if cancel_token is not None else CancelToken(),
            idle_future=loop.create_future(),
            run_id=run_id or uuid4().hex,
        )

    def cancel(self) -> None:
        self.cancel_token.cancel()

    def is_cancelled(self) -> bool:
        return self.cancel_token.is_cancelled()

    def is_idle(self) -> bool:
        return self.idle_future.done()

    async def wait_idle(self) -> None:
        await self.idle_future

    def mark_idle(self) -> None:
        if not self.idle_future.done():
            self.idle_future.set_result(None)


__all__ = [
    "CancelToken",
    "RunHandle",
]
