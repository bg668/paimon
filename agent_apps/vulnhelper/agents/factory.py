from __future__ import annotations

from ..agentsdk.adapters.openai_chatcompletions import OpenAIChatCompletionsAdapter

from ..config import VulnHelperConfig
from ..tools.filter_cached_results import FilterCachedResultsTool
from ..tools.query_vulns import QueryVulnsTool
from .analyst import AnalystAgentRunner, build_analyst_agent
from .executor import ExecutorAgentRunner, build_executor_agent
from .planner import PlannerAgentRunner, build_planner_agent


class AgentFactory:
    def __init__(
        self,
        *,
        config: VulnHelperConfig,
        adapters: dict[str, OpenAIChatCompletionsAdapter | None],
        session_manager,
        query_tool: QueryVulnsTool,
        filter_tool: FilterCachedResultsTool,
    ) -> None:
        self._config = config
        self._adapters = adapters
        self._session_manager = session_manager
        self._query_tool = query_tool
        self._filter_tool = filter_tool

    def _adapter_for(self, profile_ref: str) -> OpenAIChatCompletionsAdapter | None:
        return self._adapters.get(profile_ref)

    def build_planner_agent(self) -> PlannerAgentRunner:
        adapter = self._adapter_for(self._config.planner_subagent.provider_ref)
        stream_fn = adapter.stream_message if adapter is not None else None
        model = self._config.planner_model if adapter is not None else None
        return build_planner_agent(
            stream_fn,
            model,
            self._config.planner_prompt_path,
            role=self._config.planner_subagent.role,
            temperature=self._config.planner_temperature,
            max_tokens=self._config.planner_max_tokens,
        )

    def build_executor_agent(self) -> ExecutorAgentRunner:
        adapter = self._adapter_for(self._config.executor_subagent.provider_ref)
        stream_fn = adapter.stream_message if adapter is not None else None
        model = self._config.executor_model if adapter is not None else None
        return build_executor_agent(
            stream_fn,
            model,
            self._config.executor_prompt_path,
            self._session_manager,
            self._query_tool,
            self._filter_tool,
            role=self._config.executor_subagent.role,
            temperature=self._config.executor_temperature,
            max_tokens=self._config.executor_max_tokens,
        )

    def build_analyst_agent(self) -> AnalystAgentRunner:
        adapter = self._adapter_for(self._config.analyst_subagent.provider_ref)
        stream_fn = adapter.stream_message if adapter is not None else None
        model = self._config.analyst_model if adapter is not None else None
        return build_analyst_agent(
            stream_fn,
            model,
            self._config.analyst_prompt_path,
            role=self._config.analyst_subagent.role,
            temperature=self._config.analyst_temperature,
            max_tokens=self._config.analyst_max_tokens,
        )
