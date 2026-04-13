from __future__ import annotations

from dataclasses import asdict

from ..domain.enums import SessionState
from ..domain.models import QueryPlan
from ..domain.summarizer import build_analysis_brief
from .dto import UserTurnInput, UserTurnOutput
from .request_router import RequestRouter, RoutedIntent


class VulnHelperOrchestrator:
    def __init__(
        self,
        *,
        session_manager,
        request_router: RequestRouter,
        planner_agent,
        executor_agent,
        analyst_agent,
        workflow,
        confirmation_renderer,
        report_renderer,
        table_renderer,
        query_cache,
    ) -> None:
        self._session_manager = session_manager
        self._request_router = request_router
        self._planner_agent = planner_agent
        self._executor_agent = executor_agent
        self._analyst_agent = analyst_agent
        self._workflow = workflow
        self._confirmation_renderer = confirmation_renderer
        self._report_renderer = report_renderer
        self._table_renderer = table_renderer
        self._query_cache = query_cache

    def subscribe_agent_events(self, listener):
        unsubs = [
            self._planner_agent.subscribe_events(lambda event, cancel_token: listener("planner", event, cancel_token)),
            self._executor_agent.subscribe_events(lambda event, cancel_token: listener("executor", event, cancel_token)),
            self._analyst_agent.subscribe_events(lambda event, cancel_token: listener("analyst", event, cancel_token)),
        ]

        def _unsubscribe_all() -> None:
            for unsubscribe in unsubs:
                unsubscribe()

        return _unsubscribe_all

    @staticmethod
    def _thinking_metadata(**values: str | None) -> dict[str, str]:
        return {key: value for key, value in values.items() if value}

    def _reset_agent_contexts(self) -> None:
        for agent in (self._planner_agent, self._executor_agent, self._analyst_agent):
            reset = getattr(agent, "reset", None)
            if callable(reset):
                reset()

    async def handle_text(self, *, session_id: str, text: str) -> UserTurnOutput:
        return await self.handle_turn(UserTurnInput(session_id=session_id, text=text))

    async def handle_turn(self, input: UserTurnInput) -> UserTurnOutput:
        session = self._session_manager.get_or_create(input.session_id)
        intent = self._request_router.route(session, input.text)

        if intent == RoutedIntent.RESTART:
            self._reset_agent_contexts()
            self._query_cache.clear(input.session_id)
            session = self._session_manager.reset(input.session_id)
            return UserTurnOutput(session_id=input.session_id, state=session.state.value, markdown="会话已重置。", metadata={})
        if intent == RoutedIntent.NEW_QUERY:
            return await self._handle_new_query(session, input.text)
        if intent == RoutedIntent.REFINE_PLAN:
            return await self._handle_new_query(session, input.text)
        if intent == RoutedIntent.CONFIRM:
            return await self._handle_confirmation(session)
        if intent == RoutedIntent.DRILLDOWN:
            return await self._handle_drilldown(session, input.text)
        return await self._handle_new_query(session, input.text)

    async def _handle_new_query(self, session, text: str) -> UserTurnOutput:
        if self._workflow.route_for("new_query") != ("planner",):
            return self._fail_output(session, "当前配置的 new_query 工作流暂不受支持。")
        plan, confirmation_text = await self._planner_agent.plan_query(text)
        session.state = SessionState.WAITING_FOR_CONFIRMATION
        session.planned_args = plan
        session.last_query_result = None
        session.last_report_markdown = None
        session.last_filter_spec = None
        self._query_cache.clear(session.session_id)
        self._session_manager.save(session)
        markdown = self._confirmation_renderer.render(plan, confirmation_text)
        metadata = {"planned_args": asdict(plan)}
        thinking = self._thinking_metadata(planner=self._planner_agent.last_thinking)
        if thinking:
            metadata["thinking"] = thinking
        return UserTurnOutput(session_id=session.session_id, state=session.state.value, markdown=markdown, metadata=metadata)

    async def _handle_confirmation(self, session) -> UserTurnOutput:
        if session.planned_args is None:
            return self._fail_output(session, "当前没有待确认的查询计划。")
        route = self._workflow.route_for("confirm")
        cached = None
        expert_analysis = ""
        fix_strategy = ""

        for phase in route:
            if phase == "executor":
                session.state = SessionState.EXECUTING_QUERY
                self._session_manager.save(session)
                await self._executor_agent.execute_query(session.session_id, asdict(session.planned_args))
                cached = self._query_cache.get(session.session_id)
                if cached is None:
                    return self._fail_output(session, "查询未生成缓存结果。")
                session.last_query_result = cached
            elif phase == "analyst":
                if cached is None:
                    return self._fail_output(session, "工作流配置错误：analyst 阶段缺少查询结果。")
                analysis_brief = build_analysis_brief(cached.query_plan, cached.summary, cached.matched_records)
                expert_analysis, fix_strategy = await self._analyst_agent.analyze(analysis_brief)
            else:
                return self._fail_output(session, f"当前配置的 confirm 工作流包含未支持阶段: {phase}")

        if cached is None:
            return self._fail_output(session, "当前配置的 confirm 工作流未执行查询阶段。")
        markdown = self._report_renderer.render(cached, expert_analysis, fix_strategy)
        session.state = SessionState.REPORT_READY
        session.last_report_markdown = markdown
        self._session_manager.save(session)
        metadata = {"cache_id": cached.cache_id}
        thinking = self._thinking_metadata(
            executor=self._executor_agent.last_thinking,
            analyst=self._analyst_agent.last_thinking,
        )
        if thinking:
            metadata["thinking"] = thinking
        if getattr(self._executor_agent, "last_assistant_text", None):
            metadata["executor_last_assistant_text"] = self._executor_agent.last_assistant_text
        return UserTurnOutput(session_id=session.session_id, state=session.state.value, markdown=markdown, metadata=metadata)

    async def _handle_drilldown(self, session, text: str) -> UserTurnOutput:
        if session.last_query_result is None:
            return self._fail_output(session, "当前会话没有可下钻的缓存结果。")
        if self._workflow.route_for("drilldown") != ("executor",):
            return self._fail_output(session, "当前配置的 drilldown 工作流暂不受支持。")
        result = await self._executor_agent.execute_filter(session.session_id, text)
        cached = self._query_cache.get(session.session_id)
        if cached is not None:
            session.last_query_result = cached
        session.last_filter_spec = result.filter_spec
        session.state = SessionState.DRILLDOWN_READY
        self._session_manager.save(session)
        markdown = self._table_renderer.render(result.rows)
        return UserTurnOutput(
            session_id=session.session_id,
            state=session.state.value,
            markdown=markdown,
            metadata={
                "filtered_count": result.filtered_count,
                **({"thinking": self._thinking_metadata(executor=self._executor_agent.last_thinking)} if self._executor_agent.last_thinking else {}),
            },
        )

    def _fail_output(self, session, message: str) -> UserTurnOutput:
        session.state = SessionState.FAILED
        session.metadata["last_error"] = message
        self._session_manager.save(session)
        metadata = {"error": message}
        if getattr(self._executor_agent, "last_assistant_text", None):
            metadata["executor_last_assistant_text"] = self._executor_agent.last_assistant_text
        if getattr(self._executor_agent, "last_thinking", None):
            metadata["thinking"] = self._thinking_metadata(executor=self._executor_agent.last_thinking)
        return UserTurnOutput(session_id=session.session_id, state=session.state.value, markdown=message, metadata=metadata)
