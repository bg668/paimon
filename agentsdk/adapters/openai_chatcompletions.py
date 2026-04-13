from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from inspect import isawaitable
from typing import Any, AsyncIterator, Awaitable, Iterable, Mapping, Sequence

from agentsdk.runtime.config import AgentLoopConfig
from agentsdk.runtime.errors import OpenAIAdapterError
from agentsdk.runtime.models import (
    AgentContext,
    AssistantMessage,
    AssistantMessageEvent,
    AssistantMessageEventStream,
    AssistantStreamDone,
    AssistantStreamError,
    AssistantStreamStart,
    AssistantTextDelta,
    AssistantThinkingDelta,
    AssistantToolCallDelta,
    ImageContent,
    ModelInfo,
    TextContent,
    ThinkingContent,
    TokenUsage,
    ToolCallContent,
    UsageCost,
    utc_timestamp_ms,
)
from agentsdk.runtime.run_control import CancelToken


def _maybe_get(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


def _first_item(items: Any) -> Any:
    if items is None:
        return None
    if isinstance(items, Sequence) and not isinstance(items, (str, bytes, bytearray)):
        return items[0] if items else None
    return None


async def _maybe_await(value: Awaitable[Any] | Any) -> Any:
    if isawaitable(value):
        return await value
    return value


def _safe_json_dumps(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _normalize_usage(raw_usage: Any) -> TokenUsage:
    if raw_usage is None:
        return TokenUsage()

    prompt_tokens_details = _maybe_get(raw_usage, "prompt_tokens_details", {}) or {}
    completion_tokens_details = _maybe_get(raw_usage, "completion_tokens_details", {}) or {}
    return TokenUsage(
        input=int(_maybe_get(raw_usage, "prompt_tokens", 0) or 0),
        output=int(_maybe_get(raw_usage, "completion_tokens", 0) or 0),
        cache_read=int(_maybe_get(prompt_tokens_details, "cached_tokens", 0) or 0),
        cache_write=int(_maybe_get(completion_tokens_details, "cached_tokens", 0) or 0),
        total_tokens=int(_maybe_get(raw_usage, "total_tokens", 0) or 0),
        cost=UsageCost(),
    )


def _map_finish_reason(raw_reason: Any) -> str:
    if raw_reason in {"stop", "tool_calls", "length", "content_filter", "error", "aborted"}:
        return str(raw_reason)
    return "unknown" if raw_reason is not None else "stop"


def _normalize_tool_call_arguments(arguments: Any) -> Any:
    if isinstance(arguments, str):
        parsed = _parse_partial_json(arguments)
        return parsed if parsed is not None else {}
    return arguments if arguments is not None else {}


def _repair_partial_json(raw: str) -> str | None:
    if not raw.strip():
        return None

    stack: list[str] = []
    in_string = False
    escape = False
    for char in raw:
        if escape:
            escape = False
            continue
        if char == "\\":
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            stack.append("}")
        elif char == "[":
            stack.append("]")
        elif char in {"}", "]"} and stack and stack[-1] == char:
            stack.pop()

    repaired = raw
    if in_string:
        repaired += '"'
    repaired += "".join(reversed(stack))
    return repaired


def _parse_partial_json(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        repaired = _repair_partial_json(raw)
        if repaired is None:
            return None
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            return None


def _text_from_content_parts(parts: Sequence[Any]) -> str | None:
    segments: list[str] = []
    for part in parts:
        part_type = _maybe_get(part, "type")
        if part_type == "text":
            text = _maybe_get(part, "text")
            if text:
                segments.append(str(text))
    return "".join(segments) if segments else None


def _message_to_openai_dict(message: Any) -> dict[str, Any]:
    role = _maybe_get(message, "role")
    if role == "user":
        content_parts = []
        for block in _maybe_get(message, "content", []) or []:
            if getattr(block, "type", None) == "text":
                content_parts.append({"type": "text", "text": block.text})
            elif getattr(block, "type", None) == "image":
                image_payload: dict[str, Any] = {"url": block.image_url}
                if block.detail is not None:
                    image_payload["detail"] = block.detail
                content_parts.append({"type": "image_url", "image_url": image_payload})
        return {"role": "user", "content": content_parts}

    if role == "assistant":
        text_content = "".join(
            block.text for block in (_maybe_get(message, "content", []) or []) if getattr(block, "type", None) == "text"
        )
        tool_calls = []
        for block in _maybe_get(message, "content", []) or []:
            if getattr(block, "type", None) == "toolCall":
                tool_calls.append(
                    {
                        "id": block.id,
                        "type": "function",
                        "function": {
                            "name": block.name,
                            "arguments": _safe_json_dumps(block.arguments),
                        },
                    }
                )
        payload: dict[str, Any] = {"role": "assistant", "content": text_content or None}
        if tool_calls:
            payload["tool_calls"] = tool_calls
        return payload

    if role == "toolResult":
        content_parts = []
        for block in _maybe_get(message, "content", []) or []:
            if getattr(block, "type", None) == "text":
                content_parts.append(block.text)
            elif getattr(block, "type", None) == "image":
                content_parts.append(f"[image:{block.image_url}]")
        return {
            "role": "tool",
            "tool_call_id": _maybe_get(message, "tool_call_id"),
            "content": "\n".join(content_parts),
        }

    raise OpenAIAdapterError(f"Unsupported message role: {role}")


def _tool_to_openai_dict(tool: Any) -> dict[str, Any]:
    schema = getattr(tool, "input_schema", None) or {"type": "object", "properties": {}, "additionalProperties": True}
    function_payload = {"name": tool.name, "parameters": schema}
    description = getattr(tool, "description", None)
    if description:
        function_payload["description"] = description
    return {"type": "function", "function": function_payload}


def _base_assistant_message(model: ModelInfo) -> AssistantMessage:
    return AssistantMessage(
        content=[],
        stop_reason="stop",
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=TokenUsage(),
        timestamp=utc_timestamp_ms(),
    )


def _completion_message_to_assistant_message(model: ModelInfo, response: Any) -> AssistantMessage:
    choice = _first_item(_maybe_get(response, "choices"))
    if choice is None:
        raise OpenAIAdapterError("OpenAI response did not contain a choice")

    message = _maybe_get(choice, "message")
    finish_reason = _map_finish_reason(_maybe_get(choice, "finish_reason"))
    assistant = _base_assistant_message(model)
    assistant.stop_reason = finish_reason
    assistant.model = str(_maybe_get(response, "model", model.id) or model.id)
    assistant.usage = _normalize_usage(_maybe_get(response, "usage"))

    text_content = _maybe_get(message, "content")
    if isinstance(text_content, str) and text_content:
        assistant.content.append(TextContent(text=text_content))
    elif isinstance(text_content, Sequence) and not isinstance(text_content, (str, bytes, bytearray)):
        text_from_parts = _text_from_content_parts(text_content)
        if text_from_parts:
            assistant.content.append(TextContent(text=text_from_parts))

    for tool_call in _maybe_get(message, "tool_calls", []) or []:
        function = _maybe_get(tool_call, "function", {}) or {}
        assistant.content.append(
            ToolCallContent(
                id=str(_maybe_get(tool_call, "id", "") or ""),
                name=str(_maybe_get(function, "name", "") or ""),
                arguments=_normalize_tool_call_arguments(_maybe_get(function, "arguments")),
            )
        )
    return assistant


def _error_assistant_message(model: ModelInfo, message: str, aborted: bool = False) -> AssistantMessage:
    assistant = _base_assistant_message(model)
    assistant.stop_reason = "aborted" if aborted else "error"
    assistant.error_message = message
    assistant.content = [TextContent(text="")]
    return assistant


class _ImmediateEventStream(AssistantMessageEventStream):
    def __init__(self, events: Iterable[AssistantMessageEvent], final_message: AssistantMessage) -> None:
        self._events = list(events)
        self._final_message = final_message

    def __aiter__(self) -> AsyncIterator[AssistantMessageEvent]:
        async def _iterate() -> AsyncIterator[AssistantMessageEvent]:
            for event in self._events:
                yield event

        return _iterate()

    async def result(self) -> AssistantMessage:
        return self._final_message


class _StreamingEventStream(AssistantMessageEventStream):
    def __init__(
        self,
        model: ModelInfo,
        chunk_stream: AsyncIterator[Any],
        cancel_token: CancelToken | None = None,
    ) -> None:
        self._model = model
        self._chunk_stream = chunk_stream
        self._cancel_token = cancel_token
        self._final_future: asyncio.Future[AssistantMessage] = asyncio.get_running_loop().create_future()
        self._started = False

    def __aiter__(self) -> AsyncIterator[AssistantMessageEvent]:
        if self._started:
            raise RuntimeError("AssistantMessageEventStream can only be iterated once")
        self._started = True
        return self._iterate()

    async def result(self) -> AssistantMessage:
        return await self._final_future

    async def _iterate(self) -> AsyncIterator[AssistantMessageEvent]:
        partial = _base_assistant_message(self._model)
        text_index: int | None = None
        thinking_index: int | None = None
        tool_index_to_content_index: dict[int, int] = {}
        tool_index_to_raw_arguments: dict[int, str] = {}
        last_usage = TokenUsage()

        yield AssistantStreamStart(partial=partial)

        try:
            async for chunk in self._chunk_stream:
                if self._cancel_token is not None and self._cancel_token.is_cancelled():
                    final_message = _error_assistant_message(self._model, "Operation cancelled", aborted=True)
                    if not self._final_future.done():
                        self._final_future.set_result(final_message)
                    yield AssistantStreamError(partial=final_message, error_message=final_message.error_message)
                    return

                choice = _first_item(_maybe_get(chunk, "choices"))
                if choice is None:
                    continue

                delta = _maybe_get(choice, "delta", {}) or {}
                usage = _maybe_get(chunk, "usage")
                if usage is not None:
                    last_usage = _normalize_usage(usage)

                content_delta = _maybe_get(delta, "content")
                if isinstance(content_delta, str) and content_delta:
                    if text_index is None:
                        partial.content.append(TextContent(text=""))
                        text_index = len(partial.content) - 1
                    text_block = partial.content[text_index]
                    if isinstance(text_block, TextContent):
                        text_block.text += content_delta
                    yield AssistantTextDelta(partial=partial, delta=content_delta, index=text_index)

                reasoning_delta = _maybe_get(delta, "reasoning") or _maybe_get(delta, "reasoning_content")
                if isinstance(reasoning_delta, str) and reasoning_delta:
                    if thinking_index is None:
                        partial.content.append(ThinkingContent(thinking=""))
                        thinking_index = len(partial.content) - 1
                    thinking_block = partial.content[thinking_index]
                    if isinstance(thinking_block, ThinkingContent):
                        thinking_block.thinking += reasoning_delta
                    yield AssistantThinkingDelta(partial=partial, delta=reasoning_delta, index=thinking_index)

                for tool_delta in _maybe_get(delta, "tool_calls", []) or []:
                    tool_index = int(_maybe_get(tool_delta, "index", 0) or 0)
                    content_index = tool_index_to_content_index.get(tool_index)
                    if content_index is None:
                        partial.content.append(ToolCallContent())
                        content_index = len(partial.content) - 1
                        tool_index_to_content_index[tool_index] = content_index

                    tool_block = partial.content[content_index]
                    if not isinstance(tool_block, ToolCallContent):
                        tool_block = ToolCallContent()
                        partial.content[content_index] = tool_block

                    tool_id = _maybe_get(tool_delta, "id")
                    if tool_id:
                        tool_block.id = str(tool_id)

                    function_delta = _maybe_get(tool_delta, "function", {}) or {}
                    tool_name = _maybe_get(function_delta, "name")
                    if tool_name:
                        tool_block.name = str(tool_name)

                    arguments_delta = str(_maybe_get(function_delta, "arguments", "") or "")
                    if arguments_delta:
                        raw_arguments = tool_index_to_raw_arguments.get(tool_index, "") + arguments_delta
                        tool_index_to_raw_arguments[tool_index] = raw_arguments
                        parsed_arguments = _parse_partial_json(raw_arguments)
                        if parsed_arguments is not None:
                            tool_block.arguments = parsed_arguments
                    elif tool_block.arguments == {}:
                        tool_block.arguments = {}

                    yield AssistantToolCallDelta(
                        partial=partial,
                        tool_call=replace(tool_block),
                        index=content_index,
                        arguments_delta=arguments_delta,
                    )

                finish_reason = _maybe_get(choice, "finish_reason")
                if finish_reason is not None:
                    partial.stop_reason = _map_finish_reason(finish_reason)
                    partial.usage = last_usage
                    final_message = replace(partial, content=list(partial.content), usage=last_usage)
                    if not self._final_future.done():
                        self._final_future.set_result(final_message)
                    yield AssistantStreamDone(partial=final_message)
                    return

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            final_message = _error_assistant_message(self._model, str(exc), aborted=False)
            if not self._final_future.done():
                self._final_future.set_result(final_message)
            yield AssistantStreamError(partial=final_message, error_message=final_message.error_message)
            return

        partial.usage = last_usage
        final_message = replace(partial, content=list(partial.content), usage=last_usage)
        if not self._final_future.done():
            self._final_future.set_result(final_message)
        yield AssistantStreamDone(partial=final_message)


class OpenAIChatCompletionsAdapter:
    def __init__(self, client: Any) -> None:
        self._client = client

    async def create_message(
        self,
        model: ModelInfo,
        context: AgentContext,
        options: AgentLoopConfig,
        cancel_token: CancelToken | None = None,
    ) -> AssistantMessage:
        try:
            if cancel_token is not None:
                cancel_token.raise_if_cancelled()
            request_options = await self._build_request_options(model, context, options, stream=False)
            response = await _maybe_await(self._client.chat.completions.create(**request_options))
            return _completion_message_to_assistant_message(model, response)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return _error_assistant_message(model, str(exc), aborted=bool(cancel_token and cancel_token.is_cancelled()))

    async def stream_message(
        self,
        model: ModelInfo,
        context: AgentContext,
        options: AgentLoopConfig,
        cancel_token: CancelToken | None = None,
    ) -> AssistantMessageEventStream:
        try:
            if cancel_token is not None:
                cancel_token.raise_if_cancelled()

            request_options = await self._build_request_options(model, context, options, stream=True)
            chunk_stream = await _maybe_await(self._client.chat.completions.create(**request_options))
            return _StreamingEventStream(model=model, chunk_stream=chunk_stream, cancel_token=cancel_token)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            final_message = _error_assistant_message(
                model,
                str(exc),
                aborted=bool(cancel_token and cancel_token.is_cancelled()),
            )
            return _ImmediateEventStream(
                [AssistantStreamError(partial=final_message, error_message=final_message.error_message)],
                final_message,
            )

    async def _build_request_options(
        self,
        model: ModelInfo,
        context: AgentContext,
        options: AgentLoopConfig,
        *,
        stream: bool,
    ) -> dict[str, Any]:
        api_key = options.api_key
        if api_key is None and options.get_api_key is not None:
            api_key = await _maybe_await(options.get_api_key(model.provider))

        messages = [_message_to_openai_dict(message) for message in context.messages]
        if context.system_prompt:
            messages = [{"role": "system", "content": context.system_prompt}, *messages]

        request_options: dict[str, Any] = {
            "model": model.id,
            "messages": messages,
            "stream": stream,
        }
        if context.tools:
            request_options["tools"] = [_tool_to_openai_dict(tool) for tool in context.tools]
        if api_key is not None:
            request_options["api_key"] = api_key
        if options.temperature is not None:
            request_options["temperature"] = options.temperature
        if options.top_p is not None:
            request_options["top_p"] = options.top_p
        if options.max_tokens is not None:
            request_options["max_tokens"] = options.max_tokens
        if options.metadata:
            request_options["metadata"] = dict(options.metadata)
        if stream:
            request_options["stream_options"] = {"include_usage": True}
        return request_options


__all__ = [
    "OpenAIChatCompletionsAdapter",
]
