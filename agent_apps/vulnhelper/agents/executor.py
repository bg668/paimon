from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass

from ..agentsdk import (
    AfterToolCallResult,
    Agent,
    AgentOptions,
    BeforeToolCallResult,
    TextContent,
    ToolExecutionMode,
    ToolResultMessage,
)

from ..config import read_text
from ..domain.enums import SessionState
from ..domain.models import FilterSpec
from ..domain.query_understanding import parse_filter_spec
from ..domain.normalization import normalize_risk_levels, normalize_vuln_id
from ..tools.filter_cached_results import FilterCachedResultsTool
from ..tools.query_vulns import QueryVulnsTool
from .introspection import extract_assistant_thinking, extract_latest_assistant_text
from .prompt_injection import build_system_prompt


@dataclass(slots=True)
class FilterExecutionResult:
    filter_spec: FilterSpec
    rows: list[dict[str, str]]
    filtered_count: int


class ExecutorAgentRunner:
    def __init__(self, agent: Agent | None, query_tool: QueryVulnsTool, filter_tool: FilterCachedResultsTool) -> None:
        self._agent = agent
        self._query_tool = query_tool
        self._filter_tool = filter_tool
        self._last_thinking: str | None = None
        self._last_assistant_text: str | None = None

    @property
    def last_thinking(self) -> str | None:
        return self._last_thinking

    @property
    def last_assistant_text(self) -> str | None:
        return self._last_assistant_text

    def subscribe_events(self, listener):
        if self._agent is None:
            return lambda: None
        return self._agent.subscribe(listener)

    def reset(self) -> None:
        self._last_thinking = None
        self._last_assistant_text = None
        if self._agent is not None:
            self._agent.reset()

    async def execute_query(self, session_id: str, approved_plan: dict) -> None:
        if self._agent is None:
            self._last_thinking = None
            self._last_assistant_text = None
            await self._query_tool.execute(
                "query-local",
                {
                    "session_id": session_id,
                    "plan": approved_plan,
                },
            )
            return

        instruction = (
            "Current mode: query_execution\n"
            f"Approved session_id: {session_id}\n"
            f"Approved query_plan: {approved_plan}\n"
            "Required tool call shape:\n"
            "{\n"
            '  "session_id": "<approved session_id>",\n'
            '  "plan": <approved query_plan>\n'
            "}\n"
            "Use query_vulns with exactly the approved query_plan and session_id.\n"
            "Do not explain. Do not answer in prose."
        )
        start_index = len(self._agent.state.messages)
        details = await self._prompt_until_tool_result(self._query_tool.name, instruction)
        self._last_thinking = extract_assistant_thinking(self._agent.state.messages, start_index)
        self._last_assistant_text = extract_latest_assistant_text(self._agent.state.messages[start_index:])
        if details is None:
            hint = f" Last assistant text: {self._last_assistant_text}" if self._last_assistant_text else ""
            raise RuntimeError(f"ExecutorAgent did not produce a query_vulns tool call.{hint}")

    async def execute_filter(self, session_id: str, user_request: str) -> FilterExecutionResult:
        if self._agent is None:
            self._last_thinking = None
            self._last_assistant_text = None
            filter_spec = parse_filter_spec(user_request)
            result = await self._filter_tool.execute(
                "filter-local",
                {
                    "session_id": session_id,
                    "filter_spec": {
                        "risk_levels": filter_spec.risk_levels,
                        "has_public_poc": filter_spec.has_public_poc,
                        "has_solution": filter_spec.has_solution,
                        "malicious_only": filter_spec.malicious_only,
                        "cve_ids": filter_spec.cve_ids,
                        "limit": filter_spec.limit,
                    },
                },
            )
            details = result.details if isinstance(result.details, dict) else {}
            return FilterExecutionResult(
                filter_spec=filter_spec,
                rows=list(details.get("rows", [])),
                filtered_count=int(details.get("filtered_count", 0)),
            )
        instruction = (
            "Current mode: drilldown_filter\n"
            f"Session id: {session_id}\n"
            f"User drilldown request: {user_request}\n"
            "Required tool call shape:\n"
            "{\n"
            '  "session_id": "<current session id>",\n'
            '  "filter_spec": {...}\n'
            "}\n"
            "Compile the request into filter_cached_results arguments only.\n"
            "Do not explain. Do not answer in prose."
        )
        start_index = len(self._agent.state.messages)
        details = await self._prompt_until_tool_result(self._filter_tool.name, instruction)
        self._last_thinking = extract_assistant_thinking(self._agent.state.messages, start_index)
        self._last_assistant_text = extract_latest_assistant_text(self._agent.state.messages[start_index:])
        if details is None:
            hint = f" Last assistant text: {self._last_assistant_text}" if self._last_assistant_text else ""
            raise RuntimeError(f"ExecutorAgent did not produce a filter_cached_results tool call.{hint}")
        applied = details.get("applied_filters", {}) if isinstance(details, dict) else {}
        filter_spec = FilterSpec(
            risk_levels=normalize_risk_levels(applied.get("risk_levels")) or None,
            has_public_poc=applied.get("has_public_poc"),
            has_solution=applied.get("has_solution"),
            malicious_only=applied.get("malicious_only"),
            cve_ids=[normalize_vuln_id(value) for value in applied.get("cve_ids", [])] or None,
            limit=applied.get("limit"),
        )
        return FilterExecutionResult(
            filter_spec=filter_spec,
            rows=list(details.get("rows", [])),
            filtered_count=int(details.get("filtered_count", 0)),
        )

    async def _prompt_until_tool_result(self, tool_name: str, instruction: str, retries: int = 2) -> dict | None:
        if self._agent is None:
            return None

        prompt = instruction
        for _ in range(retries):
            start_index = len(self._agent.state.messages)
            await self._agent.prompt(prompt)
            await self._agent.wait_for_idle()
            details = self._latest_tool_result_details(tool_name, start_index)
            if details is not None:
                return details
            prompt = (
                "上一轮没有调用必需工具。你必须立即发起合法工具调用，不能输出自然语言，不能解释。\n"
                + instruction
            )
        return None

    def _latest_tool_result_details(self, tool_name: str, start_index: int) -> dict | None:
        messages = list(self._agent.state.messages)[start_index:] if self._agent is not None else []
        for message in reversed(messages):
            if isinstance(message, ToolResultMessage) and message.tool_name == tool_name and not message.is_error:
                return message.details if isinstance(message.details, dict) else {}
        return None


def build_executor_agent(
    stream_fn,
    model,
    system_prompt_path,
    session_manager,
    query_tool: QueryVulnsTool,
    filter_tool: FilterCachedResultsTool,
    *,
    role: str = "",
    temperature: float = 0.0,
    max_tokens: int | None = None,
) -> ExecutorAgentRunner:
    def before_tool_call(context, cancel_token):
        del cancel_token
        session_id = context.args.get("session_id")
        session = session_manager.get_or_create(session_id)
        if context.tool_call.name == query_tool.name:
            # The orchestrator marks the session as EXECUTING_QUERY immediately
            # after the user confirms and before delegating the actual tool call.
            if session.state not in {SessionState.WAITING_FOR_CONFIRMATION, SessionState.EXECUTING_QUERY}:
                return BeforeToolCallResult(block=True, reason="当前会话尚未确认查询，不能执行漏洞检索。")
            approved = session.planned_args
            if approved is None or context.args.get("plan") != asdict(approved):
                return BeforeToolCallResult(block=True, reason="工具参数与已确认查询计划不一致。")
        if context.tool_call.name == filter_tool.name:
            if session.state not in {SessionState.REPORT_READY, SessionState.DRILLDOWN_READY}:
                return BeforeToolCallResult(block=True, reason="当前会话没有可下钻的查询结果。")
            if session.last_query_result is None:
                return BeforeToolCallResult(block=True, reason="当前会话没有可下钻的缓存结果。")
        return None

    def after_tool_call(context, cancel_token):
        del cancel_token
        if context.is_error:
            return AfterToolCallResult(content=[TextContent(text="工具执行失败，请检查当前会话状态或输入参数。")], is_error=True)
        count = None
        if isinstance(context.result.details, dict):
            count = context.result.details.get("filtered_count")
        text = f"{context.tool_call.name} succeeded"
        if count is not None:
            text += f": matched={count}"
        return AfterToolCallResult(content=[TextContent(text=text)], is_error=False)

    if stream_fn is None or model is None:
        return ExecutorAgentRunner(None, query_tool, filter_tool)

    prompt_text = read_text(system_prompt_path)
    agent = Agent(
        AgentOptions(
            system_prompt=build_system_prompt(role, prompt_text),
            model=model,
            stream_fn=stream_fn,
            tools=[query_tool, filter_tool],
            before_tool_call=before_tool_call,
            after_tool_call=after_tool_call,
            tool_execution=ToolExecutionMode.SEQUENTIAL,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    )
    return ExecutorAgentRunner(agent, query_tool, filter_tool)
