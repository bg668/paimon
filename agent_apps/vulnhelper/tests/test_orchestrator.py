import asyncio

from agent_apps.vulnhelper.application.orchestrator import VulnHelperOrchestrator
from agent_apps.vulnhelper.application.request_router import RequestRouter, RoutedIntent
from agent_apps.vulnhelper.application.session_manager import SessionManager
from agent_apps.vulnhelper.domain.enums import EntryMode, SessionState, UserGoal
from agent_apps.vulnhelper.domain.models import QueryPlan


def test_router_defaults_to_new_query() -> None:
    manager = SessionManager()
    session = manager.get_or_create("s1")
    router = RequestRouter()
    assert router.route(session, "tensorflow-cpu 2.4.1 有漏洞吗") == RoutedIntent.NEW_QUERY


def test_router_treats_new_command_as_restart() -> None:
    manager = SessionManager()
    session = manager.get_or_create("s1")
    router = RequestRouter()
    assert router.route(session, "/new") == RoutedIntent.RESTART


class ResettableAgent:
    def __init__(self) -> None:
        self.reset_called = False
        self.last_thinking = None

    def subscribe_events(self, _listener):
        return lambda: None

    def reset(self) -> None:
        self.reset_called = True


class ResettableQueryCache:
    def __init__(self) -> None:
        self.cleared_session_ids: list[str] = []

    def clear(self, session_id: str) -> None:
        self.cleared_session_ids.append(session_id)


def test_new_command_resets_session_and_agent_contexts() -> None:
    manager = SessionManager()
    session = manager.get_or_create("s1")
    session.state = SessionState.WAITING_FOR_CONFIRMATION
    session.planned_args = QueryPlan(
        entry_mode=EntryMode.PRODUCT_VERSION,
        product="apache-superset",
        user_goal=UserGoal.TRIAGE,
    )
    manager.save(session)

    planner = ResettableAgent()
    executor = ResettableAgent()
    analyst = ResettableAgent()
    query_cache = ResettableQueryCache()
    orchestrator = VulnHelperOrchestrator(
        session_manager=manager,
        request_router=RequestRouter(),
        planner_agent=planner,
        executor_agent=executor,
        analyst_agent=analyst,
        workflow=type("Workflow", (), {"route_for": lambda self, _name: ()})(),
        confirmation_renderer=object(),
        report_renderer=object(),
        table_renderer=object(),
        query_cache=query_cache,
    )

    output = asyncio.run(orchestrator.handle_text(session_id="s1", text="/new"))
    reset_session = manager.get_or_create("s1")

    assert output.state == "idle"
    assert output.markdown == "会话已重置。"
    assert reset_session.state == SessionState.IDLE
    assert reset_session.planned_args is None
    assert planner.reset_called is True
    assert executor.reset_called is True
    assert analyst.reset_called is True
    assert query_cache.cleared_session_ids == ["s1"]

