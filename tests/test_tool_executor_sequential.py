from __future__ import annotations

import asyncio
from dataclasses import dataclass

from paimonsdk.runtime.config import AfterToolCallResult, AgentLoopConfig, BeforeToolCallResult
from paimonsdk.runtime.models import (
    AgentContext,
    AgentToolResult,
    AssistantMessage,
    ModelInfo,
    TextContent,
    ToolArtifactRef,
    ToolCallContent,
    ToolExecutionMode,
    ToolResultStatus,
)
from paimonsdk.runtime.tool_executor import execute_tool_calls


@dataclass
class FakeTool:
    name: str
    label: str
    input_schema: dict
    description: str | None = None
    prepare_arguments: callable | None = None
    updates: list[AgentToolResult] | None = None
    result: AgentToolResult | None = None
    error: Exception | None = None

    async def execute(self, tool_call_id, params, cancel_token=None, on_update=None):
        if self.updates:
            for update in self.updates:
                if on_update is not None:
                    on_update(update)
        if self.error is not None:
            raise self.error
        return self.result or AgentToolResult(content=[TextContent(text=f"ok:{params}")], details=params)


def _identity_stream(*args, **kwargs):
    raise AssertionError("stream_fn should not be used in tool executor tests")


def test_tool_executor_sequential_full_chain_and_error_collection():
    async def _run() -> None:
        call_log = []
        alpha_tool = FakeTool(
            name="alpha",
            label="Alpha",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
            prepare_arguments=lambda raw: {"text": raw["text"].upper()} if "text" in raw else raw,
            updates=[AgentToolResult(content=[TextContent(text="working")], details={"step": 1})],
            result=AgentToolResult(content=[TextContent(text="done-alpha")], details={"status": "ok"}),
        )
        blocked_tool = FakeTool(
            name="blocked",
            label="Blocked",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        )
        boom_tool = FakeTool(
            name="boom",
            label="Boom",
            input_schema={
                "type": "object",
                "properties": {"n": {"type": "integer"}},
                "required": ["n"],
            },
            error=RuntimeError("boom failed"),
        )

        async def before_tool_call(context, cancel_token):
            call_log.append(("before", context.tool_call.id, context.args))
            if context.tool_call.name == "blocked":
                return BeforeToolCallResult(block=True, reason="blocked by policy")
            return None

        async def after_tool_call(context, cancel_token):
            call_log.append(("after", context.tool_call.id, context.status.value))
            if context.tool_call.name == "alpha":
                return AfterToolCallResult(
                    result=AgentToolResult(
                        content=[TextContent(text="after-alpha")],
                        details={"after": True},
                        artifacts=[ToolArtifactRef(artifact_id="artifact-1", kind="report", uri="file:///tmp/report.md")],
                        status=ToolResultStatus.OK,
                    )
                )
            return None

        emitted = []

        async def emit(event):
            emitted.append(event)

        assistant_message = AssistantMessage(
            content=[
                ToolCallContent(id="1", name="alpha", arguments={"text": "hello"}),
                ToolCallContent(id="2", name="missing", arguments={"x": 1}),
                ToolCallContent(id="3", name="blocked", arguments={"text": "x"}),
                ToolCallContent(id="4", name="boom", arguments={"n": 3}),
                ToolCallContent(id="5", name="alpha", arguments={"wrong": "shape"}),
            ],
            api="chat.completions",
            provider="openai",
            model="gpt-test",
        )
        context = AgentContext(system_prompt="system", messages=[], tools=[alpha_tool, blocked_tool, boom_tool])
        config = AgentLoopConfig(
            model=ModelInfo(id="gpt-test", provider="openai", api="chat.completions"),
            stream_fn=_identity_stream,
            convert_to_llm=lambda messages: list(messages),
            before_tool_call=before_tool_call,
            after_tool_call=after_tool_call,
            tool_execution=ToolExecutionMode.SEQUENTIAL,
        )

        results = await execute_tool_calls(
            current_context=context,
            assistant_message=assistant_message,
            config=config,
            emit=emit,
        )

        assert [item.tool_call_id for item in results] == ["1", "2", "3", "4", "5"]
        assert results[0].content[0].text == "after-alpha"
        assert results[0].details == {"after": True}
        assert results[0].status == ToolResultStatus.OK
        assert results[0].artifacts[0].artifact_id == "artifact-1"
        assert results[0].is_error is False
        assert results[1].is_error is True
        assert results[1].status == ToolResultStatus.ERROR
        assert results[1].error is not None
        assert results[1].content[0].text == "Tool missing not found"
        assert results[2].is_error is True
        assert results[2].status == ToolResultStatus.BLOCKED
        assert results[2].error is not None
        assert results[2].error.code == "tool_blocked"
        assert results[2].content[0].text == "blocked by policy"
        assert results[3].is_error is True
        assert results[3].status == ToolResultStatus.ERROR
        assert results[3].content[0].text == "boom failed"
        assert results[4].is_error is True
        assert results[4].status == ToolResultStatus.ERROR
        assert "args.text is required" in results[4].content[0].text

        assert call_log == [
            ("before", "1", {"text": "HELLO"}),
            ("after", "1", "ok"),
            ("before", "3", {"text": "x"}),
            ("before", "4", {"n": 3}),
            ("after", "4", "error"),
        ]
        assert [event.type for event in emitted] == [
            "tool_execution_start",
            "tool_execution_update",
            "tool_execution_end",
            "message_start",
            "message_end",
            "tool_execution_start",
            "tool_execution_end",
            "message_start",
            "message_end",
            "tool_execution_start",
            "tool_execution_end",
            "message_start",
            "message_end",
            "tool_execution_start",
            "tool_execution_end",
            "message_start",
            "message_end",
            "tool_execution_start",
            "tool_execution_end",
            "message_start",
            "message_end",
        ]
        assert emitted[1].partial_result.content[0].text == "working"

    asyncio.run(_run())
