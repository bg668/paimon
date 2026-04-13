from __future__ import annotations

import asyncio

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


class ArtifactTool:
    name = "artifact_tool"
    label = "Artifact Tool"
    description = "Returns an artifact reference"
    input_schema = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
    prepare_arguments = None

    async def execute(self, tool_call_id, params, cancel_token=None, on_update=None):
        return AgentToolResult(
            content=[TextContent(text=f"generated:{params['name']}")],
            details={"name": params["name"]},
            artifacts=[
                ToolArtifactRef(
                    artifact_id=f"artifact:{tool_call_id}",
                    kind="report",
                    uri=f"file:///tmp/{params['name']}.md",
                    name=f"{params['name']}.md",
                    mime_type="text/markdown",
                )
            ],
            status=ToolResultStatus.OK,
        )


def _identity_stream(*args, **kwargs):
    raise AssertionError("stream_fn should not be used in tool executor tests")


def test_tool_contract_preserves_artifacts_and_structured_errors():
    async def _run() -> None:
        tool = ArtifactTool()
        assistant_message = AssistantMessage(
            content=[ToolCallContent(id="t1", name="artifact_tool", arguments={"name": "summary"})],
            api="chat.completions",
            provider="openai",
            model="gpt-test",
        )
        emitted = []

        async def emit(event):
            emitted.append(event)

        async def after_tool_call(context, cancel_token):
            assert context.status == ToolResultStatus.OK
            assert context.error is None
            assert context.result.artifacts[0].artifact_id == "artifact:t1"
            return AfterToolCallResult(result=context.result)

        config = AgentLoopConfig(
            model=ModelInfo(id="gpt-test", provider="openai", api="chat.completions"),
            stream_fn=_identity_stream,
            convert_to_llm=lambda messages: list(messages),
            after_tool_call=after_tool_call,
            tool_execution=ToolExecutionMode.SEQUENTIAL,
        )
        results = await execute_tool_calls(
            current_context=AgentContext(system_prompt="system", messages=[], tools=[tool]),
            assistant_message=assistant_message,
            config=config,
            emit=emit,
        )

        assert results[0].status == ToolResultStatus.OK
        assert results[0].artifacts[0].uri == "file:///tmp/summary.md"
        end_event = next(event for event in emitted if event.type == "tool_execution_end")
        assert end_event.result.artifacts[0].artifact_id == "artifact:t1"

    asyncio.run(_run())


def test_before_tool_call_can_block_with_structured_result():
    async def _run() -> None:
        tool = ArtifactTool()
        assistant_message = AssistantMessage(
            content=[ToolCallContent(id="t2", name="artifact_tool", arguments={"name": "blocked"})],
            api="chat.completions",
            provider="openai",
            model="gpt-test",
        )

        async def emit(event):
            return None

        async def before_tool_call(context, cancel_token):
            return BeforeToolCallResult(
                block=True,
                result=AgentToolResult(
                    content=[TextContent(text="blocked by policy")],
                    details={"policy": "no-export"},
                    artifacts=[ToolArtifactRef(artifact_id="audit:1", kind="audit_log", uri="memory://audit/1")],
                    status=ToolResultStatus.BLOCKED,
                ),
            )

        config = AgentLoopConfig(
            model=ModelInfo(id="gpt-test", provider="openai", api="chat.completions"),
            stream_fn=_identity_stream,
            convert_to_llm=lambda messages: list(messages),
            before_tool_call=before_tool_call,
            tool_execution=ToolExecutionMode.SEQUENTIAL,
        )
        results = await execute_tool_calls(
            current_context=AgentContext(system_prompt="system", messages=[], tools=[tool]),
            assistant_message=assistant_message,
            config=config,
            emit=emit,
        )

        assert results[0].status == ToolResultStatus.BLOCKED
        assert results[0].is_error is True
        assert results[0].artifacts[0].artifact_id == "audit:1"
        assert results[0].error is not None
        assert results[0].error.code == "tool_blocked"
        assert results[0].details == {"policy": "no-export"}

    asyncio.run(_run())
