"""
AuditLog: thin wrapper around WorkspaceStore.append_event for all key lifecycle events.
"""
from schemas import Event, EventType
from workspace_store import WorkspaceStore
from typing import Optional, Any


class AuditLog:
    def __init__(self, store: WorkspaceStore) -> None:
        self._store = store

    def append_event(self, event_type: EventType, task_id: Optional[str] = None, payload: Optional[dict[str, Any]] = None) -> None:
        """Append a structured event to event_log.jsonl."""
        self._store.append_event(
            Event(event_type=event_type, task_id=task_id, payload=payload or {})
        )
