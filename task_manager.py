"""
Task management logic layer (Phase 2).

All mutating operations are immediately persisted to the workspace via
WorkspaceStore; memory is never the source of truth.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from schemas import Event, EventType, TaskNode, TaskStatus
from workspace_store import WorkspaceStore


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskManager:
    """CRUD + scheduling logic for TaskNode objects persisted on disk."""

    def __init__(self, store: WorkspaceStore) -> None:
        self._store = store

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create_task(self, task: TaskNode) -> TaskNode:
        """Persist *task* to disk and emit a task_created event."""
        self._store.write_task(task.model_dump())
        self._store.append_event(
            Event(
                event_type=EventType.TASK_CREATED,
                task_id=task.id,
                payload={"name": task.name, "priority": task.priority},
            )
        )
        return task

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------

    def get_next_runnable_task(self) -> Optional[TaskNode]:
        """Return the highest-priority pending task with no remaining blockers.

        Selection criteria (applied in order):
        1. status == pending
        2. blocked_by == []
        3. Sort: priority ascending (lower number = higher priority),
                 then created_at ascending (oldest first).
        """
        candidates = [
            TaskNode.model_validate(t)
            for t in self._store.list_tasks()
            if t.get("status") == TaskStatus.PENDING.value
            and not t.get("blocked_by", [])
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda t: (t.priority, t.created_at))
        return candidates[0]

    # ------------------------------------------------------------------
    # Status transitions
    # ------------------------------------------------------------------

    def mark_task_completed(self, task_id: str) -> TaskNode:
        """Mark *task_id* completed and remove it from all dependents' blocked_by."""
        raw = self._store.read_task(task_id)
        task = TaskNode.model_validate(raw)
        task.status = TaskStatus.COMPLETED
        task.updated_at = _utcnow_iso()
        self._store.write_task(task.model_dump())

        # Unblock dependents
        # O(n) scan — acceptable for the current workspace scale.
        # A dependency index should be added if task counts grow large.
        for dep_raw in self._store.list_tasks():
            blocked = dep_raw.get("blocked_by", [])
            if task_id in blocked:
                dep = TaskNode.model_validate(dep_raw)
                dep.blocked_by = [b for b in dep.blocked_by if b != task_id]
                dep.updated_at = _utcnow_iso()
                self._store.write_task(dep.model_dump())
                self._store.append_event(
                    Event(
                        event_type=EventType.DEPENDENCY_UNBLOCKED,
                        task_id=dep.id,
                        payload={"unblocked_by": task_id},
                    )
                )

        self._store.append_event(
            Event(
                event_type=EventType.TASK_COMPLETED,
                task_id=task_id,
                payload={},
            )
        )
        return task

    def mark_task_failed(self, task_id: str, failure_reason: str) -> TaskNode:
        """Mark *task_id* as failed with a recorded *failure_reason*."""
        raw = self._store.read_task(task_id)
        task = TaskNode.model_validate(raw)
        task.status = TaskStatus.FAILED
        task.failure_reason = failure_reason
        task.updated_at = _utcnow_iso()
        self._store.write_task(task.model_dump())
        self._store.append_event(
            Event(
                event_type=EventType.TASK_FAILED,
                task_id=task_id,
                payload={"failure_reason": failure_reason},
            )
        )
        return task
