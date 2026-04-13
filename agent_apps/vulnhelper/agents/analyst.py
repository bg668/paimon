from __future__ import annotations

import json
import re

from ..agentsdk import Agent, AgentOptions

from ..config import read_text
from .introspection import extract_assistant_thinking, extract_latest_assistant_text
from .prompt_injection import build_system_prompt


SECTION_RE = re.compile(r"\[(?P<section>[A-Z_]+)\]\s*(?P<body>.*?)(?=\n\[[A-Z_]+\]|\Z)", re.S)

def _parse_sections(text: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for match in SECTION_RE.finditer(text):
        parsed[match.group("section")] = match.group("body").strip()
    return parsed


class AnalystAgentRunner:
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

    async def analyze(self, analysis_brief: dict[str, object]) -> tuple[str, str]:
        if self._agent is None:
            self._last_thinking = None
            return _fallback_analysis(analysis_brief)
        start_index = len(self._agent.state.messages)
        await self._agent.prompt(json.dumps(analysis_brief, ensure_ascii=False))
        await self._agent.wait_for_idle()
        self._last_thinking = extract_assistant_thinking(self._agent.state.messages, start_index)
        text = extract_latest_assistant_text(self._agent.state.messages)
        sections = _parse_sections(text)
        expert = sections.get("EXPERT_ANALYSIS")
        fix = sections.get("FIX_STRATEGY")
        if expert and fix:
            return expert, fix
        return _fallback_analysis(analysis_brief)


def build_analyst_agent(
    stream_fn,
    model,
    system_prompt_path,
    *,
    role: str = "",
    temperature: float = 0.2,
    max_tokens: int | None = None,
) -> AnalystAgentRunner:
    if stream_fn is None or model is None:
        return AnalystAgentRunner(None)
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
    return AnalystAgentRunner(agent)


def _fallback_analysis(analysis_brief: dict[str, object]) -> tuple[str, str]:
    matched_count = analysis_brief.get("matched_count", 0)
    highest_risk = analysis_brief.get("highest_risk") or "unknown"
    has_public_poc = analysis_brief.get("has_public_poc")
    has_solution = analysis_brief.get("has_solution")
    global_fix = analysis_brief.get("min_fixed_version_global")
    same_branch = analysis_brief.get("min_fixed_version_same_branch")
    fallback_expert = (
        f"当前排查命中 {matched_count} 条记录，最高风险等级为 {highest_risk}。"
        f"{' 已发现公开 POC。' if has_public_poc else ''}"
        f"{' 当前记录存在修复方案。' if has_solution else ''}"
    ).strip()
    fallback_fix = f"全局建议优先关注 {global_fix or '暂无明确统一修复版本'}；同版本线可优先关注 {same_branch or '暂无明确可用修复版本'}。"
    return fallback_expert, fallback_fix
