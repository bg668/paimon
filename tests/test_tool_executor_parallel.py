from __future__ import annotations

import asyncio
from dataclasses import dataclass

from paimonsdk.runtime.config import AgentLoopConfig
from paimonsdk.runtime.models import AgentContext, AgentToolResult, AssistantMessage, ModelInfo, TextContent, ToolCallContent, ToolExecutionMode, ToolResultStatus
from paimonsdk.runtime.tool_executor import execute_tool_calls


@dataclass
class ParallelTool:
    name: str
    label: str
    delay: float
    tracker: dict
    input_schema: dict | None = None
    description: str | None = None
    prepare_arguments: callable | None = None

    async def execute(self, tool_call_id, params, cancel_token=None, on_update=None):
        self.tracker["active"] += 1
        self.tracker["max_active"] = max(self.tracker["max_active"], self.tracker["active"])
        self.tracker["starts"].append(tool_call_id)
        if on_update is not None:
            on_update(AgentToolResult(content=[TextContent(text=f"update-{tool_call_id}")], details={"id": tool_call_id}))
        await asyncio.sleep(self.delay)
        self.tracker["finishes"].append(tool_call_id)
        self.tracker["active"] -= 1
        return AgentToolResult(content=[TextContent(text=f"result-{tool_call_id}")], details={"id": tool_call_id})


def _identity_stream(*args, **kwargs):
    raise AssertionError("stream_fn should not be used in tool executor tests")


def test_tool_executor_parallel_executes_concurrently_but_finalizes_in_source_order():
    async def _run() -> None:
        tracker = {"active": 0, "max_active": 0, "starts": [], "finishes": []}
        slow_tool = ParallelTool(name="slow", label="Slow", delay=0.05, tracker=tracker)
        fast_tool = ParallelTool(name="fast", label="Fast", delay=0.01, tracker=tracker)

        assistant_message = AssistantMessage(
            content=[
                ToolCallContent(id="slow-1", name="slow", arguments={"x": 1}),
                ToolCallContent(id="fast-2", name="fast", arguments={"x": 2}),
            ],
            api="chat.completions",
            provider="openai",
            model="gpt-test",
        )
        context = AgentContext(system_prompt="system", messages=[], tools=[slow_tool, fast_tool])
        emitted = []

        async def emit(event):
            emitted.append(event)

        config = AgentLoopConfig(
            model=ModelInfo(id="gpt-test", provider="openai", api="chat.completions"),
            stream_fn=_identity_stream,
            convert_to_llm=lambda messages: list(messages),
            tool_execution=ToolExecutionMode.PARALLEL,
        )

        start = asyncio.get_running_loop().time()
        results = await execute_tool_calls(
            current_context=context,
            assistant_message=assistant_message,
            config=config,
            emit=emit,
        )
        elapsed = asyncio.get_running_loop().time() - start

        assert tracker["max_active"] == 2
        assert tracker["starts"] == ["slow-1", "fast-2"]
        assert tracker["finishes"] == ["fast-2", "slow-1"]
        assert elapsed < 0.06

        assert [item.tool_call_id for item in results] == ["slow-1", "fast-2"]
        assert [item.content[0].text for item in results] == ["result-slow-1", "result-fast-2"]
        assert all(item.status == ToolResultStatus.OK for item in results)
        assert [event.type for event in emitted] == [
            "tool_execution_start",
            "tool_execution_start",
            "tool_execution_update",
            "tool_execution_update",
            "tool_execution_end",
            "message_start",
            "message_end",
            "tool_execution_end",
            "message_start",
            "message_end",
        ]
        end_events = [event for event in emitted if event.type == "tool_execution_end"]
        assert [event.tool_call_id for event in end_events] == ["slow-1", "fast-2"]

    asyncio.run(_run())


def test_tool_executor_parallel_keeps_source_order_with_immediate_error():
    async def _run() -> None:
        tracker = {"active": 0, "max_active": 0, "starts": [], "finishes": []}
        slow_tool = ParallelTool(
            name="slow",
            label="Slow",
            delay=0.03,
            tracker=tracker,
            input_schema={"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]},
        )
        assistant_message = AssistantMessage(
            content=[
                ToolCallContent(id="a", name="slow", arguments={"x": 1}),
                ToolCallContent(id="b", name="missing", arguments={"y": 2}),
            ],
            api="chat.completions",
            provider="openai",
            model="gpt-test",
        )
        context = AgentContext(system_prompt="system", messages=[], tools=[slow_tool])
        emitted = []

        async def emit(event):
            emitted.append(event)

        config = AgentLoopConfig(
            model=ModelInfo(id="gpt-test", provider="openai", api="chat.completions"),
            stream_fn=_identity_stream,
            convert_to_llm=lambda messages: list(messages),
            tool_execution=ToolExecutionMode.PARALLEL,
        )

        results = await execute_tool_calls(
            current_context=context,
            assistant_message=assistant_message,
            config=config,
            emit=emit,
        )

        assert [item.tool_call_id for item in results] == ["a", "b"]
        assert results[0].is_error is False
        assert results[0].status == ToolResultStatus.OK
        assert results[1].is_error is True
        assert results[1].status == ToolResultStatus.ERROR
        assert results[1].error is not None
        assert results[1].content[0].text == "Tool missing not found"
        end_events = [event for event in emitted if event.type == "tool_execution_end"]
        assert [event.tool_call_id for event in end_events] == ["a", "b"]

    asyncio.run(_run())
