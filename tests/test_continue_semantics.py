from __future__ import annotations

import asyncio

from paimonsdk import Agent, AgentOptions, InvalidContinuationError
from paimonsdk.runtime.models import AgentContext, AssistantMessage, ModelInfo, TextContent, UserMessage


class ImmediateFinalStream:
    def __init__(self, final_message: AssistantMessage) -> None:
        self._final_message = final_message

    def __aiter__(self):
        async def _iterate():
            if False:
                yield None

        return _iterate()

    async def result(self) -> AssistantMessage:
        return self._final_message


def _last_user_text(message) -> str:
    if message.role != "user":
        return message.role
    for content in message.content:
        if content.type == "text":
            return content.text
    return ""


def test_continue_semantics_empty_transcript_and_assistant_boundary_errors():
    async def _run() -> None:
        model = ModelInfo(id="gpt-test", provider="openai", api="chat.completions")

        async def fake_stream_fn(model, context, config, cancel_token):
            return ImmediateFinalStream(
                AssistantMessage(content=[TextContent(text="unused")], api=model.api, provider=model.provider, model=model.id)
            )

        empty_agent = Agent(AgentOptions(model=model, stream_fn=fake_stream_fn))
        try:
            await empty_agent.continue_()
        except InvalidContinuationError:
            pass
        else:
            raise AssertionError("Expected InvalidContinuationError for empty transcript")

        assistant_only_agent = Agent(
            AgentOptions(
                model=model,
                stream_fn=fake_stream_fn,
                messages=[AssistantMessage(content=[TextContent(text="done")], api=model.api, provider=model.provider, model=model.id)],
            )
        )
        try:
            await assistant_only_agent.continue_()
        except InvalidContinuationError:
            pass
        else:
            raise AssertionError("Expected InvalidContinuationError when continuing from assistant with no queued messages")

    asyncio.run(_run())


def test_continue_semantics_steering_has_priority_over_follow_up():
    async def _run() -> None:
        model = ModelInfo(id="gpt-test", provider="openai", api="chat.completions")

        async def fake_stream_fn(model, context: AgentContext, config, cancel_token):
            text = _last_user_text(context.messages[-1])
            return ImmediateFinalStream(
                AssistantMessage(
                    content=[TextContent(text=f"reply:{text}")],
                    api=model.api,
                    provider=model.provider,
                    model=model.id,
                )
            )

        agent = Agent(
            AgentOptions(
                model=model,
                stream_fn=fake_stream_fn,
                messages=[AssistantMessage(content=[TextContent(text="seed")], api=model.api, provider=model.provider, model=model.id)],
            )
        )
        agent.steer(UserMessage(content=[TextContent(text="steer-first")]))
        agent.follow_up(UserMessage(content=[TextContent(text="follow-next")]))

        await agent.continue_()
        messages_after_continue = agent.state.messages
        assert messages_after_continue[1].role == "user"
        assert messages_after_continue[1].content[0].text == "steer-first"
        assert messages_after_continue[2].role == "assistant"
        assert messages_after_continue[2].content[0].text == "reply:steer-first"
        assert messages_after_continue[3].role == "user"
        assert messages_after_continue[3].content[0].text == "follow-next"
        assert messages_after_continue[4].role == "assistant"
        assert messages_after_continue[4].content[0].text == "reply:follow-next"

        try:
            await agent.continue_()
        except InvalidContinuationError:
            pass
        else:
            raise AssertionError("Expected InvalidContinuationError after steering and follow-up queues were both consumed")

    asyncio.run(_run())


def test_continue_from_non_assistant_reuses_existing_transcript_without_inserting_prompt():
    async def _run() -> None:
        model = ModelInfo(id="gpt-test", provider="openai", api="chat.completions")

        async def fake_stream_fn(model, context: AgentContext, config, cancel_token):
            text = _last_user_text(context.messages[-1])
            return ImmediateFinalStream(
                AssistantMessage(
                    content=[TextContent(text=f"reply:{text}")],
                    api=model.api,
                    provider=model.provider,
                    model=model.id,
                )
            )

        agent = Agent(
            AgentOptions(
                model=model,
                stream_fn=fake_stream_fn,
                messages=[UserMessage(content=[TextContent(text="resume")])],
            )
        )

        await agent.continue_()
        assert len(agent.state.messages) == 2
        assert agent.state.messages[0].role == "user"
        assert agent.state.messages[1].role == "assistant"
        assert agent.state.messages[1].content[0].text == "reply:resume"

    asyncio.run(_run())
