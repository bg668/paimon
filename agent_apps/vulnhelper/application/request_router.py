from __future__ import annotations

from enum import Enum

from ..domain.enums import SessionState
from ..domain.models import VulnSession
from ..domain.normalization import is_confirmation_text


class RoutedIntent(str, Enum):
    NEW_QUERY = "new_query"
    CONFIRM = "confirm"
    REFINE_PLAN = "refine_plan"
    DRILLDOWN = "drilldown"
    RESTART = "restart"


class RequestRouter:
    def route(self, session: VulnSession, text: str) -> RoutedIntent:
        normalized = text.strip().lower()
        if normalized in {"重置", "重新开始", "restart", "reset", "/new"}:
            return RoutedIntent.RESTART
        if session.state == SessionState.WAITING_FOR_CONFIRMATION:
            return RoutedIntent.CONFIRM if is_confirmation_text(text) else RoutedIntent.REFINE_PLAN
        if session.state in {SessionState.REPORT_READY, SessionState.DRILLDOWN_READY}:
            return RoutedIntent.DRILLDOWN
        return RoutedIntent.NEW_QUERY

