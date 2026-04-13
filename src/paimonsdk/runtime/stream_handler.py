from __future__ import annotations

from copy import deepcopy
from inspect import isawaitable
from typing import Any, Awaitable, Callable

from .config import AgentLoopConfig
from .events import AgentEvent, MessageEndEvent, MessageStartEvent, MessageUpdateEvent
from .models import AgentContext, AssistantMessage
from .run_control import CancelToken


AgentEventSink = Callable[[AgentEvent], Awaitable[None] | None]
STREAM_UPDATE_EVENT_TYPES = {"text_delta", "thinking_delta", "tool_call_delta"}


async def _maybe_await(result: Awaitable[Any] | Any) -> Any:
    if isawaitable(result):
        return await result
    return result


def _copy_message(message: AssistantMessage) -> AssistantMessage:
    return deepcopy(message)


async def stream_assistant_response(
    context: AgentContext,
    config: AgentLoopConfig,
    emit: AgentEventSink,
    cancel_token: CancelToken | None = None,
) -> AssistantMessage:
    if cancel_token is not None:
        cancel_token.raise_if_cancelled()

    messages = context.messages
    if config.transform_context is not None:
        messages = list(await _maybe_await(config.transform_context(messages, cancel_token)))

    llm_messages = list(await _maybe_await(config.convert_to_llm(messages)))
    llm_context = AgentContext(
        system_prompt=context.system_prompt,
        messages=llm_messages,
        tools=list(context.tools),
    )

    response = await _maybe_await(
        config.stream_fn(
            config.model,
            llm_context,
            config,
            cancel_token,
        )
    )

    partial_message: AssistantMessage | None = None
    added_partial = False

    async for event in response:
        if event.type == "start":
            partial_message = event.partial
            context.messages.append(partial_message)
            added_partial = True
            await _maybe_await(emit(MessageStartEvent(message=_copy_message(partial_message))))
            continue

        if event.type in STREAM_UPDATE_EVENT_TYPES and partial_message is not None:
            partial_message = event.partial
            context.messages[-1] = partial_message
            await _maybe_await(
                emit(
                    MessageUpdateEvent(
                        message=_copy_message(partial_message),
                        assistant_message_event=event,
                    )
                )
            )
            continue

        if event.type in {"done", "error"}:
            final_message = await response.result()
            if added_partial:
                context.messages[-1] = final_message
            else:
                context.messages.append(final_message)
                await _maybe_await(emit(MessageStartEvent(message=_copy_message(final_message))))
            await _maybe_await(emit(MessageEndEvent(message=final_message)))
            return final_message

    final_message = await response.result()
    if added_partial:
        context.messages[-1] = final_message
    else:
        context.messages.append(final_message)
        await _maybe_await(emit(MessageStartEvent(message=_copy_message(final_message))))
    await _maybe_await(emit(MessageEndEvent(message=final_message)))
    return final_message


__all__ = [
    "AgentEventSink",
    "STREAM_UPDATE_EVENT_TYPES",
    "stream_assistant_response",
]
