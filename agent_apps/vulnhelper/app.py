from __future__ import annotations

import asyncio
import re
import sys
import time
from pathlib import Path
from typing import TextIO
from uuid import uuid4

from dotenv import load_dotenv

from .bootstrap import build_app
from .config import build_default_config


class ThinkingStreamPrinter:
    def __init__(
        self,
        *,
        window_size: int = 96,
        min_update_interval: float = 0.08,
        output: TextIO | None = None,
        time_fn=None,
    ) -> None:
        self._window_size = max(1, window_size)
        self._min_update_interval = max(0.0, min_update_interval)
        self._output = output if output is not None else sys.stdout
        self._time_fn = time_fn or time.monotonic
        self._active_phase: str | None = None
        self._printed_any = False
        self._phase_rendered = False
        self._raw_buffer = ""
        self._last_rendered_line = ""
        self._pending_render = False
        self._last_render_at = 0.0

    def reset(self) -> None:
        self._active_phase = None
        self._printed_any = False
        self._phase_rendered = False
        self._raw_buffer = ""
        self._last_rendered_line = ""
        self._pending_render = False
        self._last_render_at = 0.0

    def _write(self, text: str) -> None:
        self._output.write(text)
        self._output.flush()

    @staticmethod
    def _normalize_thinking_text(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    def _trim_raw_buffer(self) -> None:
        raw_limit = max(self._window_size * 8, 256)
        if len(self._raw_buffer) > raw_limit:
            self._raw_buffer = self._raw_buffer[-raw_limit:]

    def _visible_text(self) -> str:
        normalized = self._normalize_thinking_text(self._raw_buffer)
        if len(normalized) <= self._window_size:
            return normalized
        return normalized[-self._window_size :]

    def _render_current_line(self, *, force: bool = False) -> None:
        if self._active_phase is None or not self._pending_render:
            return
        visible = self._visible_text()
        if not visible:
            self._pending_render = False
            return

        line = f"[Thinking:{self._active_phase}] {visible.ljust(self._window_size)}"
        if line == self._last_rendered_line and not force:
            self._pending_render = False
            return

        if self._phase_rendered:
            padding = max(0, len(self._last_rendered_line) - len(line))
            self._write("\r" + line + (" " * padding))
        else:
            self._write(line)
            self._phase_rendered = True
            self._printed_any = True

        self._last_rendered_line = line
        self._pending_render = False
        self._last_render_at = self._time_fn()

    def _start_phase(self, phase: str) -> None:
        if self._active_phase == phase:
            return
        if self._pending_render:
            self._render_current_line(force=True)
        if self._printed_any:
            self._write("\n")
        self._active_phase = phase
        self._phase_rendered = False
        self._raw_buffer = ""
        self._last_rendered_line = ""
        self._pending_render = False
        self._last_render_at = 0.0

    def on_agent_event(self, phase: str, event, _cancel_token) -> None:
        if getattr(event, "type", None) != "message_update":
            return
        assistant_event = getattr(event, "assistant_message_event", None)
        if assistant_event is None or getattr(assistant_event, "type", None) != "thinking_delta":
            return
        delta = getattr(assistant_event, "delta", "")
        if not delta:
            return

        self._start_phase(phase)
        self._raw_buffer += delta
        self._trim_raw_buffer()
        self._pending_render = True

        if not self._phase_rendered:
            self._render_current_line(force=True)
            return

        now = self._time_fn()
        if now - self._last_render_at >= self._min_update_interval:
            self._render_current_line()

    def finish(self) -> None:
        if self._pending_render:
            self._render_current_line(force=True)
        if self._printed_any:
            if self._phase_rendered and self._last_rendered_line:
                self._write("\r" + (" " * len(self._last_rendered_line)) + "\r")
            self._write("\n")
        self.reset()


def _load_runtime_config():
    load_dotenv()
    config = build_default_config()
    if not config.config_json_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config.config_json_path}")
    return config


def _humanize_state(state: str) -> str:
    mapping = {
        "idle": "空闲",
        "waiting_for_confirmation": "等待确认",
        "executing_query": "执行查询",
        "report_ready": "报告已生成",
        "drilldown_ready": "下钻结果已生成",
        "failed": "失败",
    }
    return mapping.get(state, state)


def _print_startup_banner(config_path: Path, model_id: str, base_url: str | None, session_id: str) -> None:
    print("✓ 已加载配置:", config_path.name)
    print("✓ 使用模型:", model_id)
    print("✓ Base URL:", base_url or "默认")
    print("✓ Session ID:", session_id)
    print()
    print("=" * 50)
    print("VulnHelper CLI")
    print("输入 'exit' 或 'quit' 退出")
    print("=" * 50)


def _print_output(output) -> None:
    print(f"[状态] {_humanize_state(output.state)} ({output.state})")
    print(output.markdown)


async def main() -> None:
    try:
        config = _load_runtime_config()
    except (FileNotFoundError, ValueError) as exc:
        print(f"✗ 错误: {exc}")
        return
    missing_profiles = config.missing_api_key_profile_refs()
    if missing_profiles:
        joined = ", ".join(missing_profiles)
        print(f"✗ 错误: 以下 provider profile 未读取到 API Key: {joined}")
        return

    app = build_app(config)
    session_id = str(uuid4())
    thinking_printer = ThinkingStreamPrinter()
    unsubscribe = app.subscribe_agent_events(thinking_printer.on_agent_event)

    _print_startup_banner(config.config_json_path, config.planner_model.id, config.base_url, session_id)

    try:
        if len(sys.argv) > 1:
            for text in sys.argv[1:]:
                print(f"\n👤 你: {text}")
                print("\n🤖 VulnHelper:")
                thinking_printer.reset()
                try:
                    output = await app.handle_text(session_id=session_id, text=text)
                    thinking_printer.finish()
                    _print_output(output)
                except Exception as exc:
                    thinking_printer.finish()
                    print(f"[异常: {exc}]")
            return

        while True:
            try:
                text = input("\n👤 你: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n\n👋 再见！")
                break

            if text.lower() in {"exit", "quit", "退出"}:
                print("\n👋 再见！")
                break
            if not text:
                continue

            print("\n🤖 VulnHelper:")
            thinking_printer.reset()
            try:
                output = await app.handle_text(session_id=session_id, text=text)
                thinking_printer.finish()
                _print_output(output)
            except Exception as exc:
                thinking_printer.finish()
                print(f"[异常: {exc}]")
    finally:
        unsubscribe()


if __name__ == "__main__":
    asyncio.run(main())
