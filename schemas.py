"""
Strong-typed data models for the multi-agent system (Phase 1).
All timestamps are ISO 8601 strings produced/validated via datetime.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class EventType(str, Enum):
    TASK_CREATED = "task_created"
    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    DEPENDENCY_UNBLOCKED = "dependency_unblocked"


# ---------------------------------------------------------------------------
# Core models
# ---------------------------------------------------------------------------

class TaskNode(BaseModel):
    """Represents a single unit of work in the task graph."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    description: str = ""
    status: TaskStatus = TaskStatus.PENDING
    priority: int = Field(default=10, ge=0)
    blocked_by: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=_utcnow_iso)
    updated_at: str = Field(default_factory=_utcnow_iso)
    failure_reason: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _validate_iso8601(cls, v: Any) -> str:
        if isinstance(v, datetime):
            return v.isoformat()
        # Validate that the string is parseable as ISO 8601
        datetime.fromisoformat(str(v))
        return str(v)


class State(BaseModel):
    """Workspace-level state snapshot."""

    workspace_id: str = Field(default_factory=lambda: str(uuid4()))
    created_at: str = Field(default_factory=_utcnow_iso)
    updated_at: str = Field(default_factory=_utcnow_iso)
    data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _validate_iso8601(cls, v: Any) -> str:
        if isinstance(v, datetime):
            return v.isoformat()
        datetime.fromisoformat(str(v))
        return str(v)


class Event(BaseModel):
    """Immutable audit-log entry."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    event_type: EventType
    task_id: Optional[str] = None
    timestamp: str = Field(default_factory=_utcnow_iso)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp", mode="before")
    @classmethod
    def _validate_iso8601(cls, v: Any) -> str:
        if isinstance(v, datetime):
            return v.isoformat()
        datetime.fromisoformat(str(v))
        return str(v)


class ToolResult(BaseModel):
    """Result returned by a tool invocation."""

    tool_name: str
    task_id: str
    success: bool
    output: Any = None
    error: Optional[str] = None
    executed_at: str = Field(default_factory=_utcnow_iso)

    @field_validator("executed_at", mode="before")
    @classmethod
    def _validate_iso8601(cls, v: Any) -> str:
        if isinstance(v, datetime):
            return v.isoformat()
        datetime.fromisoformat(str(v))
        return str(v)
