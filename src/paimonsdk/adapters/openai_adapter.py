from __future__ import annotations

from typing import Any

from paimonsdk.adapters._openai_common import OpenAIRequestConfig
from paimonsdk.adapters.openai_chatcompletions import OpenAIChatCompletionsAdapter
from paimonsdk.adapters.openai_responses import OpenAIResponsesAdapter
from paimonsdk.runtime.errors import OpenAIAdapterError
from paimonsdk.runtime.models import AgentContext, AssistantMessage, AssistantMessageEventStream, ModelInfo


class OpenAIAdapter:
    def __init__(self, client: Any, request_config: OpenAIRequestConfig | None = None) -> None:
        self._client = client
        self._request_config = request_config or OpenAIRequestConfig()
        self._adapters = {
            "chat.completions": OpenAIChatCompletionsAdapter(client, self._request_config),
            "responses": OpenAIResponsesAdapter(client, self._request_config),
        }

    def with_request_config(self, **overrides: Any) -> "OpenAIAdapter":
        return OpenAIAdapter(
            self._client,
            request_config=self._request_config.merged(**overrides),
        )

    def _resolve_adapter(self, api: str) -> Any:
        adapter = self._adapters.get(api)
        if adapter is None:
            raise OpenAIAdapterError(
                f"Unsupported OpenAI model.api {api!r}; expected 'chat.completions' or 'responses'"
            )
        return adapter

    async def create_message(
        self,
        model: ModelInfo,
        context: AgentContext,
        options: Any,
        cancel_token: Any = None,
    ) -> AssistantMessage:
        adapter = self._resolve_adapter(model.api)
        return await adapter.create_message(model, context, options, cancel_token)

    async def stream_message(
        self,
        model: ModelInfo,
        context: AgentContext,
        options: Any,
        cancel_token: Any = None,
    ) -> AssistantMessageEventStream:
        adapter = self._resolve_adapter(model.api)
        return await adapter.stream_message(model, context, options, cancel_token)


__all__ = [
    "OpenAIAdapter",
]
