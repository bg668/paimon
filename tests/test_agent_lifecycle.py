from __future__ import annotations

import asyncio

from paimonsdk import Agent, AgentAlreadyRunningError, AgentOptions
from paimonsdk.runtime.models import (
    AssistantMessage,
    AssistantStreamError,
    AssistantStreamStart,
    ModelInfo,
    TextContent,
)


class DelayedFinalStream:
    def __init__(self, final_message: AssistantMessage, release: asyncio.Event) -> None:
        self._final_message = final_message
        self._release = release

    def __aiter__(self):
        async def _iterate():
            await self._release.wait()
            if False:
                yield None

        return _iterate()

    async def result(self) -> AssistantMessage:
        return self._final_message


class AbortableStream:
    def __init__(self, partial: AssistantMessage, final_message: AssistantMessage, started: asyncio.Event, cancel_token) -> None:
        self._partial = partial
        self._final_message = final_message
        self._started = started
        self._cancel_token = cancel_token

    def __aiter__(self):
        async def _iterate():
            self._started.set()
            yield AssistantStreamStart(partial=self._partial)
            await self._cancel_token.wait_cancelled()
            yield AssistantStreamError(partial=self._final_message, error_message=self._final_message.error_message)

        return _iterate()

    async def result(self) -> AssistantMessage:
        return self._final_message


def test_agent_prompt_rejects_when_another_run_is_active():
    async def _run() -> None:
        release = asyncio.Event()
        model = ModelInfo(id="gpt-test", provider="openai", api="chat.completions")

        async def fake_stream_fn(model, context, config, cancel_token):
            return DelayedFinalStream(
                AssistantMessage(
                    content=[TextContent(text="done")],
                    api=model.api,
                    provider=model.provider,
                    model=model.id,
                ),
                release,
            )

        agent = Agent(AgentOptions(model=model, stream_fn=fake_stream_fn))
        first_prompt = asyncio.create_task(agent.prompt("first"))
        await asyncio.sleep(0)

        assert agent.state.is_streaming is True
        try:
            await agent.prompt("second")
        except AgentAlreadyRunningError:
            pass
        else:
            raise AssertionError("Expected AgentAlreadyRunningError when prompting during an active run")

        release.set()
        await first_prompt
        assert agent.state.is_streaming is False
        assert len(agent.state.messages) == 2

    asyncio.run(_run())


def test_agent_abort_converges_and_wait_for_idle_waits_for_agent_end_listener():
    async def _run() -> None:
        started = asyncio.Event()
        listener_release = asyncio.Event()
        model = ModelInfo(id="gpt-test", provider="openai", api="chat.completions")

        async def fake_stream_fn(model, context, config, cancel_token):
            partial = AssistantMessage(content=[TextContent(text="")], api=model.api, provider=model.provider, model=model.id)
            final = AssistantMessage(
                content=[TextContent(text="")],
                api=model.api,
                provider=model.provider,
                model=model.id,
                stop_reason="aborted",
                error_message="cancelled",
            )
            return AbortableStream(partial=partial, final_message=final, started=started, cancel_token=cancel_token)

        agent = Agent(AgentOptions(model=model, stream_fn=fake_stream_fn))

        async def listener(event, cancel_token):
            if event.type == "agent_end":
                await listener_release.wait()

        agent.subscribe(listener)
        prompt_task = asyncio.create_task(agent.prompt("first"))
        await started.wait()

        agent.abort()
        wait_task = asyncio.create_task(agent.wait_for_idle())
        await asyncio.sleep(0)
        assert wait_task.done() is False

        listener_release.set()
        await wait_task
        await prompt_task

        assert agent.state.is_streaming is False
        assert agent.state.error_message == "cancelled"
        assert agent.state.messages[-1].role == "assistant"
        assert agent.state.messages[-1].stop_reason == "aborted"

    asyncio.run(_run())
