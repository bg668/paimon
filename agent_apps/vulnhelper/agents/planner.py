from __future__ import annotations

import json
import re

from ..agentsdk import Agent, AgentOptions

from ..config import read_text
from ..domain.enums import EntryMode, UserGoal
from ..domain.models import QueryPlan
from ..domain.normalization import normalize_package_name, normalize_vuln_id
from ..domain.query_understanding import build_confirmation_text, parse_query_plan
from .introspection import extract_assistant_thinking, extract_latest_assistant_text
from .prompt_injection import build_system_prompt


SECTION_RE = re.compile(r"\[(?P<section>[A-Z_]+)\]\s*(?P<body>.*?)(?=\n\[[A-Z_]+\]|\Z)", re.S)

def _parse_sections(text: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for match in SECTION_RE.finditer(text):
        parsed[match.group("section")] = match.group("body").strip()
    return parsed


def _fallback_plan(query: str) -> tuple[QueryPlan, str]:
    plan = parse_query_plan(query)
    if plan.product is None and " " in query:
        pieces = query.split()
        if pieces:
            plan.product = normalize_package_name(pieces[0])
    if plan.vuln_id and plan.entry_mode != EntryMode.IDENTIFIER:
        plan.entry_mode = EntryMode.IDENTIFIER
    if "升级" in query or "修复" in query:
        plan.user_goal = UserGoal.FIX_VERSION
    return plan, build_confirmation_text(plan)


class PlannerAgentRunner:
    def __init__(self, agent: Agent | None) -> None:
        self._agent = agent
        self._last_thinking: str | None = None

    @property
    def last_thinking(self) -> str | None:
        return self._last_thinking

    def subscribe_events(self, listener):
        if self._agent is None:
            return lambda: None
        return self._agent.subscribe(listener)

    def reset(self) -> None:
        self._last_thinking = None
        if self._agent is not None:
            self._agent.reset()

    async def plan_query(self, user_text: str) -> tuple[QueryPlan, str]:
        if self._agent is None:
            self._last_thinking = None
            return _fallback_plan(user_text)
        start_index = len(self._agent.state.messages)
        await self._agent.prompt(user_text)
        await self._agent.wait_for_idle()
        self._last_thinking = extract_assistant_thinking(self._agent.state.messages, start_index)
        text = extract_latest_assistant_text(self._agent.state.messages)
        sections = _parse_sections(text)
        try:
            payload = json.loads(sections["PLANNED_ARGS_JSON"])
            plan = QueryPlan(
                entry_mode=EntryMode(payload.get("entry_mode", EntryMode.PRODUCT_VERSION.value)),
                product=payload.get("product"),
                version_spec=payload.get("version_spec"),
                vuln_id=payload.get("vuln_id"),
                risk_levels=payload.get("risk_levels", []),
                require_public_poc=payload.get("require_public_poc"),
                require_solution=payload.get("require_solution"),
                malicious_only=payload.get("malicious_only"),
                source_hint=payload.get("source_hint"),
                user_goal=UserGoal(payload.get("user_goal", UserGoal.TRIAGE.value)),
            )
            confirmation_text = sections.get("USER_CONFIRMATION_TEXT", "").strip()
            if confirmation_text:
                return plan, confirmation_text
        except Exception:
            pass
        return _fallback_plan(user_text)


def build_planner_agent(
    stream_fn,
    model,
    system_prompt_path,
    *,
    role: str = "",
    temperature: float = 0.1,
    max_tokens: int | None = None,
) -> PlannerAgentRunner:
    if stream_fn is None or model is None:
        return PlannerAgentRunner(None)
    prompt_text = read_text(system_prompt_path)
    agent = Agent(
        AgentOptions(
            system_prompt=build_system_prompt(role, prompt_text),
            model=model,
            stream_fn=stream_fn,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    )
    return PlannerAgentRunner(agent)
