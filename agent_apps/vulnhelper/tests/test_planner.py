from pathlib import Path

from agent_apps.vulnhelper.agents.analyst import build_analyst_agent
from agent_apps.vulnhelper.agents.executor import build_executor_agent
from agent_apps.vulnhelper.agents.planner import build_planner_agent
from agent_apps.vulnhelper.agentsdk import ModelInfo
from agent_apps.vulnhelper.application.session_manager import SessionManager
from agent_apps.vulnhelper.config import build_default_config
from agent_apps.vulnhelper.infra.session_cache import InMemoryQueryCache
from agent_apps.vulnhelper.infra.sqlite_repository import SQLiteVulnRepository
from agent_apps.vulnhelper.tools.filter_cached_results import FilterCachedResultsTool
from agent_apps.vulnhelper.tools.query_vulns import QueryVulnsTool


def test_build_planner_agent_uses_role_in_system_prompt_without_seed_messages() -> None:
    prompt_path = Path("agent_apps/vulnhelper/prompts/planner_system.txt")
    runner = build_planner_agent(
        lambda *_args, **_kwargs: None,
        ModelInfo(id="test-model", name="test-model", provider="openai", api="chat.completions"),
        prompt_path,
        role="分析用户输入，判断应该查询的本地漏洞数据库范围，如果有歧义，可与用户多次交互以确认查询范围",
    )

    assert runner._agent is not None
    assert runner._agent.state.messages == ()
    assert "角色职责：分析用户输入，判断应该查询的本地漏洞数据库范围，如果有歧义，可与用户多次交互以确认查询范围" in runner._agent.state.system_prompt
    assert "你是 VulnHelper 的数据查询规划专家" in runner._agent.state.system_prompt


def test_build_executor_agent_uses_role_in_system_prompt_without_seed_messages() -> None:
    config = build_default_config()
    cache = InMemoryQueryCache()
    query_tool = QueryVulnsTool(repository=SQLiteVulnRepository(config.db_path), query_cache=cache)
    filter_tool = FilterCachedResultsTool(query_cache=cache)
    runner = build_executor_agent(
        lambda *_args, **_kwargs: None,
        config.executor_model,
        config.executor_prompt_path,
        SessionManager(),
        query_tool,
        filter_tool,
        role=config.executor_subagent.role,
    )

    assert runner._agent is not None
    assert runner._agent.state.messages == ()
    assert f"角色职责：{config.executor_subagent.role}" in runner._agent.state.system_prompt
    assert "你是 VulnHelper 的执行代理。" in runner._agent.state.system_prompt


def test_build_analyst_agent_uses_role_in_system_prompt_without_seed_messages() -> None:
    config = build_default_config()
    runner = build_analyst_agent(
        lambda *_args, **_kwargs: None,
        config.analyst_model,
        config.analyst_prompt_path,
        role=config.analyst_subagent.role,
    )

    assert runner._agent is not None
    assert runner._agent.state.messages == ()
    assert f"角色职责：{config.analyst_subagent.role}" in runner._agent.state.system_prompt
    assert "你是 VulnHelper 的安全研判代理。" in runner._agent.state.system_prompt
