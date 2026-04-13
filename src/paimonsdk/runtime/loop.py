from __future__ import annotations

from inspect import isawaitable
from typing import Any, Awaitable, Callable

from .config import AgentLoopConfig
from .errors import InvalidContinuationError
from .events import AgentEndEvent, MessageEndEvent, MessageStartEvent, TurnEndEvent, TurnStartEvent
from .models import AgentContext, AgentMessage, AssistantMessage, ToolResultMessage, ToolCallContent
from .run_control import CancelToken
from .stream_handler import stream_assistant_response
from .tool_executor import execute_tool_calls


AgentEventSink = Callable[[Any], Awaitable[None] | None]


async def _maybe_await(value: Awaitable[Any] | Any) -> Any:
    if isawaitable(value):
        return await value
    return value


async def _pull_messages(supplier) -> list[AgentMessage]:
    if supplier is None:
        return []
    return list(await _maybe_await(supplier()))


async def run_loop(
    current_context: AgentContext,
    new_messages: list[AgentMessage],
    config: AgentLoopConfig,
    emit: AgentEventSink,
    cancel_token: CancelToken | None = None,
) -> None:
    first_turn = True
    pending_messages = await _pull_messages(config.get_steering_messages)

    while True:
        has_more_tool_calls = True

        while has_more_tool_calls or pending_messages:
            if not first_turn:
                await _maybe_await(emit(TurnStartEvent()))
            else:
                first_turn = False

            for message in pending_messages:
                await _maybe_await(emit(MessageStartEvent(message=message)))
                await _maybe_await(emit(MessageEndEvent(message=message)))
                current_context.messages.append(message)
                new_messages.append(message)
            pending_messages = []

            assistant_message = await stream_assistant_response(
                context=current_context,
                config=config,
                emit=emit,
                cancel_token=cancel_token,
            )
            new_messages.append(assistant_message)

            if assistant_message.stop_reason in ("error", "aborted"):
                await _maybe_await(emit(TurnEndEvent(message=assistant_message, tool_results=[])))
                await _maybe_await(emit(AgentEndEvent(messages=list(new_messages))))
                return

            tool_calls = [item for item in assistant_message.content if isinstance(item, ToolCallContent)]
            has_more_tool_calls = len(tool_calls) > 0

            tool_results: list[ToolResultMessage] = []
            if has_more_tool_calls:
                tool_results = await execute_tool_calls(
                    current_context=current_context,
                    assistant_message=assistant_message,
                    config=config,
                    emit=emit,
                    cancel_token=cancel_token,
                )
                for result in tool_results:
                    current_context.messages.append(result)
                    new_messages.append(result)

            await _maybe_await(emit(TurnEndEvent(message=assistant_message, tool_results=tool_results)))
            pending_messages = await _pull_messages(config.get_steering_messages)

        followups = await _pull_messages(config.get_followup_messages)
        if followups:
            pending_messages = followups
            continue

        break

    await _maybe_await(emit(AgentEndEvent(messages=list(new_messages))))


async def run_agent_loop(
    prompts: list[AgentMessage],
    context: AgentContext,
    config: AgentLoopConfig,
    emit: AgentEventSink,
    cancel_token: CancelToken | None = None,
) -> list[AgentMessage]:
    new_messages: list[AgentMessage] = list(prompts)
    current_context = AgentContext(
        system_prompt=context.system_prompt,
        messages=[*context.messages, *prompts],
        tools=list(context.tools),
    )

    await _maybe_await(emit(TurnStartEvent()))
    for prompt in prompts:
        await _maybe_await(emit(MessageStartEvent(message=prompt)))
        await _maybe_await(emit(MessageEndEvent(message=prompt)))

    await run_loop(
        current_context=current_context,
        new_messages=new_messages,
        config=config,
        emit=emit,
        cancel_token=cancel_token,
    )
    return new_messages


async def run_agent_loop_continue(
    context: AgentContext,
    config: AgentLoopConfig,
    emit: AgentEventSink,
    cancel_token: CancelToken | None = None,
) -> list[AgentMessage]:
    if not context.messages:
        raise InvalidContinuationError("no messages to continue from")
    if context.messages[-1].role == "assistant":
        raise InvalidContinuationError("cannot continue from assistant")

    new_messages: list[AgentMessage] = []
    current_context = AgentContext(
        system_prompt=context.system_prompt,
        messages=list(context.messages),
        tools=list(context.tools),
    )

    await _maybe_await(emit(TurnStartEvent()))
    await run_loop(
        current_context=current_context,
        new_messages=new_messages,
        config=config,
        emit=emit,
        cancel_token=cancel_token,
    )
    return new_messages


__all__ = [
    "AgentEventSink",
    "run_agent_loop",
    "run_agent_loop_continue",
    "run_loop",
]
