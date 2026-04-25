from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any, AsyncIterator, Mapping, Sequence

from paimonsdk.adapters._openai_common import (
    ImmediateEventStream,
    OpenAIRequestConfig,
    base_assistant_message,
    error_assistant_message,
    maybe_await,
    maybe_get,
    merge_metadata,
    normalize_tool_call_arguments,
    normalize_usage_from_counts,
    parse_partial_json,
    resolve_api_key,
    safe_json_dumps,
)
from paimonsdk.runtime.config import AgentLoopConfig
from paimonsdk.runtime.errors import OpenAIAdapterError
from paimonsdk.runtime.models import (
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
    ToolExecutionMode,
)
from paimonsdk.runtime.run_control import CancelToken


def _normalize_usage(raw_usage: Any) -> TokenUsage:
    if raw_usage is None:
        return normalize_usage_from_counts()

    input_details = maybe_get(raw_usage, "input_tokens_details", {}) or {}
    return normalize_usage_from_counts(
        input_tokens=int(maybe_get(raw_usage, "input_tokens", 0) or 0),
        output_tokens=int(maybe_get(raw_usage, "output_tokens", 0) or 0),
        cache_read=int(maybe_get(input_details, "cached_tokens", 0) or 0),
        total_tokens=int(maybe_get(raw_usage, "total_tokens", 0) or 0),
    )


def _tool_result_output_to_text(message: Any) -> str:
    segments: list[str] = []
    for block in maybe_get(message, "content", []) or []:
        if getattr(block, "type", None) == "text":
            segments.append(block.text)
        elif getattr(block, "type", None) == "image":
            segments.append(f"[image:{block.image_url}]")
    return "\n".join(segments)


def _user_content_to_responses_items(blocks: Sequence[Any]) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    for block in blocks:
        if getattr(block, "type", None) == "text":
            content.append({"type": "input_text", "text": block.text})
        elif getattr(block, "type", None) == "image":
            image_payload: dict[str, Any] = {
                "type": "input_image",
                "image_url": block.image_url,
                "detail": block.detail or "auto",
            }
            content.append(image_payload)
    return content


def _assistant_text_from_content(blocks: Sequence[Any]) -> str:
    return "".join(block.text for block in blocks if getattr(block, "type", None) == "text")


def _message_to_responses_input_items(message: Any) -> list[dict[str, Any]]:
    role = maybe_get(message, "role")
    if role == "user":
        return [
            {
                "type": "message",
                "role": "user",
                "content": _user_content_to_responses_items(maybe_get(message, "content", []) or []),
            }
        ]

    if role == "assistant":
        items: list[dict[str, Any]] = []
        content = maybe_get(message, "content", []) or []
        text_content = _assistant_text_from_content(content)
        if text_content:
            items.append(
                {
                    "type": "message",
                    "role": "assistant",
                    "content": text_content,
                }
            )
        for block in content:
            if getattr(block, "type", None) == "toolCall":
                items.append(
                    {
                        "type": "function_call",
                        "call_id": block.id,
                        "name": block.name,
                        "arguments": safe_json_dumps(block.arguments),
                    }
                )
        return items

    if role == "toolResult":
        return [
            {
                "type": "function_call_output",
                "call_id": maybe_get(message, "tool_call_id"),
                "output": _tool_result_output_to_text(message),
            }
        ]

    raise OpenAIAdapterError(f"Unsupported message role for responses input: {role}")


def _tool_to_responses_dict(tool: Any) -> dict[str, Any]:
    schema = getattr(tool, "input_schema", None) or {"type": "object", "properties": {}, "additionalProperties": True}
    payload: dict[str, Any] = {
        "type": "function",
        "name": tool.name,
        "parameters": schema,
    }
    description = getattr(tool, "description", None)
    if description:
        payload["description"] = description
    return payload


def _append_output_message_content(assistant: AssistantMessage, item: Any) -> None:
    for content_part in maybe_get(item, "content", []) or []:
        part_type = maybe_get(content_part, "type")
        if part_type == "output_text":
            text = maybe_get(content_part, "text")
            if text:
                assistant.content.append(TextContent(text=str(text)))
        elif part_type == "refusal":
            refusal = maybe_get(content_part, "refusal")
            if refusal:
                assistant.content.append(TextContent(text=str(refusal)))


def _reasoning_text_from_item(item: Any) -> str:
    segments: list[str] = []
    for content_part in maybe_get(item, "content", []) or []:
        text = maybe_get(content_part, "text")
        if text:
            segments.append(str(text))
    if segments:
        return "".join(segments)

    for summary_part in maybe_get(item, "summary", []) or []:
        text = maybe_get(summary_part, "text")
        if text:
            segments.append(str(text))
    return "".join(segments)


def _response_has_function_calls(response: Any) -> bool:
    for item in maybe_get(response, "output", []) or []:
        if maybe_get(item, "type") == "function_call":
            return True
    return False


def _map_response_stop_reason(response: Any) -> str:
    status = maybe_get(response, "status")
    if status == "failed":
        return "error"
    if status == "cancelled":
        return "aborted"
    if status == "incomplete":
        reason = maybe_get(maybe_get(response, "incomplete_details"), "reason")
        if reason == "max_output_tokens":
            return "length"
        if reason == "content_filter":
            return "content_filter"
    if _response_has_function_calls(response):
        return "tool_calls"
    return "stop"


def _response_to_assistant_message(model: ModelInfo, response: Any) -> AssistantMessage:
    assistant = base_assistant_message(model)
    assistant.model = str(maybe_get(response, "model", model.id) or model.id)
    assistant.usage = _normalize_usage(maybe_get(response, "usage"))
    assistant.stop_reason = _map_response_stop_reason(response)

    response_error = maybe_get(response, "error")
    if response_error is not None and assistant.stop_reason == "error":
        assistant.error_message = str(maybe_get(response_error, "message", "Response failed"))

    for item in maybe_get(response, "output", []) or []:
        item_type = maybe_get(item, "type")
        if item_type == "message":
            _append_output_message_content(assistant, item)
        elif item_type == "reasoning":
            reasoning_text = _reasoning_text_from_item(item)
            if reasoning_text:
                assistant.content.append(ThinkingContent(thinking=reasoning_text))
        elif item_type == "function_call":
            assistant.content.append(
                ToolCallContent(
                    id=str(maybe_get(item, "call_id", "") or maybe_get(item, "id", "") or ""),
                    name=str(maybe_get(item, "name", "") or ""),
                    arguments=normalize_tool_call_arguments(maybe_get(item, "arguments")),
                )
            )
    return assistant


def _ensure_text_block(partial: AssistantMessage, key: tuple[int, int], mapping: dict[tuple[int, int], int]) -> int:
    content_index = mapping.get(key)
    if content_index is None:
        partial.content.append(TextContent(text=""))
        content_index = len(partial.content) - 1
        mapping[key] = content_index
    return content_index


def _ensure_reasoning_block(partial: AssistantMessage, output_index: int, mapping: dict[int, int]) -> int:
    content_index = mapping.get(output_index)
    if content_index is None:
        partial.content.append(ThinkingContent(thinking=""))
        content_index = len(partial.content) - 1
        mapping[output_index] = content_index
    return content_index


def _ensure_tool_block(
    partial: AssistantMessage,
    output_index: int,
    mapping: dict[int, int],
    tool_state: dict[int, dict[str, Any]],
) -> int:
    content_index = mapping.get(output_index)
    if content_index is None:
        state = tool_state.setdefault(output_index, {})
        partial.content.append(
            ToolCallContent(
                id=str(state.get("call_id", "") or ""),
                name=str(state.get("name", "") or ""),
                arguments=state.get("arguments", {}),
            )
        )
        content_index = len(partial.content) - 1
        mapping[output_index] = content_index
    return content_index


def _populate_from_output_item(
    partial: AssistantMessage,
    item: Any,
    output_index: int,
    *,
    text_mapping: dict[tuple[int, int], int],
    reasoning_mapping: dict[int, int],
    tool_mapping: dict[int, int],
    tool_state: dict[int, dict[str, Any]],
) -> None:
    item_type = maybe_get(item, "type")
    if item_type == "message":
        for content_index, content_part in enumerate(maybe_get(item, "content", []) or []):
            part_type = maybe_get(content_part, "type")
            if part_type == "output_text":
                text = str(maybe_get(content_part, "text", "") or "")
                if not text:
                    continue
                block_index = _ensure_text_block(partial, (output_index, content_index), text_mapping)
                text_block = partial.content[block_index]
                if isinstance(text_block, TextContent) and not text_block.text:
                    text_block.text = text
            elif part_type == "refusal":
                refusal = str(maybe_get(content_part, "refusal", "") or "")
                if not refusal:
                    continue
                block_index = _ensure_text_block(partial, (output_index, content_index), text_mapping)
                text_block = partial.content[block_index]
                if isinstance(text_block, TextContent) and not text_block.text:
                    text_block.text = refusal
    elif item_type == "reasoning":
        reasoning_text = _reasoning_text_from_item(item)
        if reasoning_text:
            block_index = _ensure_reasoning_block(partial, output_index, reasoning_mapping)
            thinking_block = partial.content[block_index]
            if isinstance(thinking_block, ThinkingContent) and not thinking_block.thinking:
                thinking_block.thinking = reasoning_text
    elif item_type == "function_call":
        state = tool_state.setdefault(output_index, {})
        state["call_id"] = str(maybe_get(item, "call_id", "") or maybe_get(item, "id", "") or "")
        state["name"] = str(maybe_get(item, "name", "") or "")
        raw_arguments = str(maybe_get(item, "arguments", "") or "")
        if raw_arguments:
            state["raw_arguments"] = raw_arguments
            parsed_arguments = parse_partial_json(raw_arguments)
            if parsed_arguments is not None:
                state["arguments"] = parsed_arguments
        else:
            state.setdefault("arguments", {})
        block_index = _ensure_tool_block(partial, output_index, tool_mapping, tool_state)
        tool_block = partial.content[block_index]
        if isinstance(tool_block, ToolCallContent):
            tool_block.id = state["call_id"]
            tool_block.name = state["name"]
            tool_block.arguments = state.get("arguments", {})


class _ResponsesStreamingEventStream(AssistantMessageEventStream):
    def __init__(
        self,
        model: ModelInfo,
        event_stream: AsyncIterator[Any],
        cancel_token: CancelToken | None = None,
    ) -> None:
        self._model = model
        self._event_stream = event_stream
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
        partial = base_assistant_message(self._model)
        partial.usage = TokenUsage()
        text_mapping: dict[tuple[int, int], int] = {}
        reasoning_mapping: dict[int, int] = {}
        tool_mapping: dict[int, int] = {}
        tool_state: dict[int, dict[str, Any]] = {}

        yield AssistantStreamStart(partial=partial)

        try:
            async for event in self._event_stream:
                if self._cancel_token is not None and self._cancel_token.is_cancelled():
                    final_message = error_assistant_message(self._model, "Operation cancelled", aborted=True)
                    if not self._final_future.done():
                        self._final_future.set_result(final_message)
                    yield AssistantStreamError(partial=final_message, error_message=final_message.error_message)
                    return

                event_type = maybe_get(event, "type")
                if event_type == "response.output_item.added":
                    _populate_from_output_item(
                        partial,
                        maybe_get(event, "item"),
                        int(maybe_get(event, "output_index", 0) or 0),
                        text_mapping=text_mapping,
                        reasoning_mapping=reasoning_mapping,
                        tool_mapping=tool_mapping,
                        tool_state=tool_state,
                    )
                    continue

                if event_type == "response.output_text.delta":
                    output_index = int(maybe_get(event, "output_index", 0) or 0)
                    content_index = int(maybe_get(event, "content_index", 0) or 0)
                    delta = str(maybe_get(event, "delta", "") or "")
                    if not delta:
                        continue
                    block_index = _ensure_text_block(partial, (output_index, content_index), text_mapping)
                    text_block = partial.content[block_index]
                    if isinstance(text_block, TextContent):
                        text_block.text += delta
                    yield AssistantTextDelta(partial=partial, delta=delta, index=block_index)
                    continue

                if event_type in {"response.reasoning_text.delta", "response.reasoning_summary_text.delta"}:
                    output_index = int(maybe_get(event, "output_index", 0) or 0)
                    delta = str(maybe_get(event, "delta", "") or "")
                    if not delta:
                        continue
                    block_index = _ensure_reasoning_block(partial, output_index, reasoning_mapping)
                    thinking_block = partial.content[block_index]
                    if isinstance(thinking_block, ThinkingContent):
                        thinking_block.thinking += delta
                    yield AssistantThinkingDelta(partial=partial, delta=delta, index=block_index)
                    continue

                if event_type == "response.function_call_arguments.delta":
                    output_index = int(maybe_get(event, "output_index", 0) or 0)
                    state = tool_state.setdefault(output_index, {})
                    block_index = _ensure_tool_block(partial, output_index, tool_mapping, tool_state)
                    tool_block = partial.content[block_index]
                    if not isinstance(tool_block, ToolCallContent):
                        continue
                    delta = str(maybe_get(event, "delta", "") or "")
                    raw_arguments = str(state.get("raw_arguments", "") or "") + delta
                    state["raw_arguments"] = raw_arguments
                    parsed_arguments = parse_partial_json(raw_arguments)
                    if parsed_arguments is not None:
                        state["arguments"] = parsed_arguments
                        tool_block.arguments = parsed_arguments
                    else:
                        tool_block.arguments = state.get("arguments", {})
                    tool_block.id = str(state.get("call_id", "") or "")
                    tool_block.name = str(state.get("name", "") or "")
                    yield AssistantToolCallDelta(
                        partial=partial,
                        tool_call=replace(tool_block),
                        index=block_index,
                        arguments_delta=delta,
                    )
                    continue

                if event_type in {"response.output_item.done", "response.function_call_arguments.done"}:
                    output_index = int(maybe_get(event, "output_index", 0) or 0)
                    item = maybe_get(event, "item")
                    if item is not None:
                        _populate_from_output_item(
                            partial,
                            item,
                            output_index,
                            text_mapping=text_mapping,
                            reasoning_mapping=reasoning_mapping,
                            tool_mapping=tool_mapping,
                            tool_state=tool_state,
                        )
                    elif event_type == "response.function_call_arguments.done":
                        state = tool_state.setdefault(output_index, {})
                        state["name"] = str(maybe_get(event, "name", "") or state.get("name", "") or "")
                        raw_arguments = str(maybe_get(event, "arguments", "") or "")
                        if raw_arguments:
                            state["raw_arguments"] = raw_arguments
                            parsed_arguments = parse_partial_json(raw_arguments)
                            if parsed_arguments is not None:
                                state["arguments"] = parsed_arguments
                        block_index = _ensure_tool_block(partial, output_index, tool_mapping, tool_state)
                        tool_block = partial.content[block_index]
                        if isinstance(tool_block, ToolCallContent):
                            tool_block.id = str(state.get("call_id", "") or "")
                            tool_block.name = str(state.get("name", "") or "")
                            tool_block.arguments = state.get("arguments", {})
                    continue

                if event_type in {"response.completed", "response.incomplete"}:
                    response = maybe_get(event, "response")
                    final_message = _response_to_assistant_message(self._model, response)
                    if not self._final_future.done():
                        self._final_future.set_result(final_message)
                    yield AssistantStreamDone(partial=final_message)
                    return

                if event_type == "response.failed":
                    response = maybe_get(event, "response")
                    final_message = _response_to_assistant_message(self._model, response)
                    if not self._final_future.done():
                        self._final_future.set_result(final_message)
                    yield AssistantStreamError(
                        partial=final_message,
                        error_message=final_message.error_message or "Response failed",
                    )
                    return

                if event_type == "error":
                    final_message = error_assistant_message(self._model, str(maybe_get(event, "message", "Response error")))
                    if not self._final_future.done():
                        self._final_future.set_result(final_message)
                    yield AssistantStreamError(partial=final_message, error_message=final_message.error_message)
                    return

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            final_message = error_assistant_message(self._model, str(exc), aborted=False)
            if not self._final_future.done():
                self._final_future.set_result(final_message)
            yield AssistantStreamError(partial=final_message, error_message=final_message.error_message)
            return

        final_message = replace(partial, content=list(partial.content))
        if not self._final_future.done():
            self._final_future.set_result(final_message)
        yield AssistantStreamDone(partial=final_message)


class OpenAIResponsesAdapter:
    def __init__(self, client: Any, request_config: OpenAIRequestConfig | None = None) -> None:
        self._client = client
        self._request_config = request_config or OpenAIRequestConfig()

    def with_request_config(self, **overrides: Any) -> "OpenAIResponsesAdapter":
        return OpenAIResponsesAdapter(
            self._client,
            request_config=self._request_config.merged(**overrides),
        )

    async def create_message(
        self,
        model: ModelInfo,
        context: AgentContext,
        options: AgentLoopConfig,
        cancel_token: CancelToken | None = None,
    ) -> AssistantMessage:
        try:
            self._ensure_supported_api(model)
            if cancel_token is not None:
                cancel_token.raise_if_cancelled()
            request_options = await self._build_request_options(model, context, options, stream=False)
            response = await maybe_await(self._client.responses.create(**request_options))
            return _response_to_assistant_message(model, response)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return error_assistant_message(model, str(exc), aborted=bool(cancel_token and cancel_token.is_cancelled()))

    async def stream_message(
        self,
        model: ModelInfo,
        context: AgentContext,
        options: AgentLoopConfig,
        cancel_token: CancelToken | None = None,
    ) -> AssistantMessageEventStream:
        try:
            self._ensure_supported_api(model)
            if cancel_token is not None:
                cancel_token.raise_if_cancelled()
            request_options = await self._build_request_options(model, context, options, stream=True)
            event_stream = await maybe_await(self._client.responses.create(**request_options))
            return _ResponsesStreamingEventStream(model=model, event_stream=event_stream, cancel_token=cancel_token)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            final_message = error_assistant_message(
                model,
                str(exc),
                aborted=bool(cancel_token and cancel_token.is_cancelled()),
            )
            return ImmediateEventStream(
                [AssistantStreamError(partial=final_message, error_message=final_message.error_message)],
                final_message,
            )

    def _ensure_supported_api(self, model: ModelInfo) -> None:
        if model.api != "responses":
            raise OpenAIAdapterError(f"OpenAIResponsesAdapter only supports model.api='responses', got {model.api!r}")

    async def _build_request_options(
        self,
        model: ModelInfo,
        context: AgentContext,
        options: AgentLoopConfig,
        *,
        stream: bool,
    ) -> dict[str, Any]:
        api_key = await resolve_api_key(self._request_config, model.provider)

        input_items: list[dict[str, Any]] = []
        for message in context.messages:
            input_items.extend(_message_to_responses_input_items(message))

        request_options: dict[str, Any] = {
            "model": model.id,
            "input": input_items,
            "stream": stream,
            "parallel_tool_calls": options.tool_execution == ToolExecutionMode.PARALLEL,
        }
        if context.system_prompt:
            request_options["instructions"] = context.system_prompt
        if context.tools:
            request_options["tools"] = [_tool_to_responses_dict(tool) for tool in context.tools]
        if api_key is not None:
            request_options["api_key"] = api_key
        if self._request_config.temperature is not None:
            request_options["temperature"] = self._request_config.temperature
        if self._request_config.top_p is not None:
            request_options["top_p"] = self._request_config.top_p
        if self._request_config.max_tokens is not None:
            request_options["max_output_tokens"] = self._request_config.max_tokens
        merged_metadata = merge_metadata(self._request_config, options.metadata)
        if merged_metadata:
            request_options["metadata"] = merged_metadata
        return request_options


__all__ = [
    "OpenAIResponsesAdapter",
]
