# miniagent

Multi-agent orchestration system implementing a Planner → Executor → Reviewer workflow loop.

## Project structure

```
schemas.py          – Pydantic data models (TaskNode, Event, EventType, …)
workspace_store.py  – File-based persistence (tasks, artifacts, event log)
task_manager.py     – CRUD + scheduling for TaskNode objects
audit_log.py        – Thin wrapper for structured event logging
tool_sandbox.py     – @tool decorator registry + safe execute_tool()
agent_runner.py     – Role-dispatched mock agent behaviors
orchestrator.py     – Planner → Executor → Reviewer workflow driver
provider_adapter.py – Pluggable LLM backend (mock by default)
demo.py             – Runnable end-to-end demonstration
tests/              – pytest test suites (Phase 1+2 and Phase 3+4)
```

## Run tests

```bash
cd /home/runner/work/miniagent/miniagent
python -m pytest tests/ -v
```

## Run the demo

```bash
cd /home/runner/work/miniagent/miniagent
python demo.py
```

## Workflow overview

1. **Planner** decomposes the request into sub-tasks.
2. **Executor** processes each sub-task and writes an artifact to `artifacts/`.
3. **Reviewer** inspects the result — rejects on first attempt, approves on retry.
4. If a task exceeds `retry_limit`, the workflow fails with `{"status": "workflow_failed"}`.
5. On success the workflow returns `{"status": "completed"}`.

All lifecycle events are appended to `event_log.jsonl` for full auditability.
