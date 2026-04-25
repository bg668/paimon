from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from inspect import isawaitable
from typing import Any, AsyncIterator, Awaitable, Callable, Iterable, Mapping, Sequence

from paimonsdk.runtime.models import (
    AssistantMessage,
    AssistantMessageEvent,
    AssistantMessageEventStream,
    TextContent,
    TokenUsage,
    UsageCost,
    utc_timestamp_ms,
)


@dataclass(slots=True)
class OpenAIRequestConfig:
    api_key: str | None = None
    api_key_resolver: Callable[[str], Awaitable[str | None] | str | None] | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def merged(self, **overrides: Any) -> "OpenAIRequestConfig":
        metadata = dict(self.metadata)
        override_metadata = overrides.pop("metadata", None)
        if isinstance(override_metadata, Mapping):
            metadata.update(override_metadata)
        merged = replace(self, metadata=metadata)
        for key, value in overrides.items():
            setattr(merged, key, value)
        return merged


def maybe_get(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


def first_item(items: Any) -> Any:
    if items is None:
        return None
    if isinstance(items, Sequence) and not isinstance(items, (str, bytes, bytearray)):
        return items[0] if items else None
    return None


async def maybe_await(value: Awaitable[Any] | Any) -> Any:
    if isawaitable(value):
        return await value
    return value


async def resolve_api_key(request_config: OpenAIRequestConfig, provider: str) -> str | None:
    api_key = request_config.api_key
    if api_key is None and request_config.api_key_resolver is not None:
        api_key = await maybe_await(request_config.api_key_resolver(provider))
    return api_key


def merge_metadata(request_config: OpenAIRequestConfig, runtime_metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
    merged = dict(request_config.metadata)
    if runtime_metadata:
        merged.update(runtime_metadata)
    return merged


def safe_json_dumps(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def repair_partial_json(raw: str) -> str | None:
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


def parse_partial_json(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        repaired = repair_partial_json(raw)
        if repaired is None:
            return None
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            return None


def normalize_tool_call_arguments(arguments: Any) -> Any:
    if isinstance(arguments, str):
        parsed = parse_partial_json(arguments)
        return parsed if parsed is not None else {}
    return arguments if arguments is not None else {}


def base_assistant_message(model: Any) -> AssistantMessage:
    return AssistantMessage(
        content=[],
        stop_reason="stop",
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=TokenUsage(),
        timestamp=utc_timestamp_ms(),
    )


def error_assistant_message(model: Any, message: str, aborted: bool = False) -> AssistantMessage:
    assistant = base_assistant_message(model)
    assistant.stop_reason = "aborted" if aborted else "error"
    assistant.error_message = message
    assistant.content = [TextContent(text="")]
    return assistant


def normalize_usage_from_counts(
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read: int = 0,
    cache_write: int = 0,
    total_tokens: int = 0,
) -> TokenUsage:
    return TokenUsage(
        input=input_tokens,
        output=output_tokens,
        cache_read=cache_read,
        cache_write=cache_write,
        total_tokens=total_tokens,
        cost=UsageCost(),
    )


class ImmediateEventStream(AssistantMessageEventStream):
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


__all__ = [
    "ImmediateEventStream",
    "OpenAIRequestConfig",
    "base_assistant_message",
    "error_assistant_message",
    "first_item",
    "maybe_await",
    "maybe_get",
    "merge_metadata",
    "normalize_tool_call_arguments",
    "normalize_usage_from_counts",
    "parse_partial_json",
    "resolve_api_key",
    "safe_json_dumps",
]
