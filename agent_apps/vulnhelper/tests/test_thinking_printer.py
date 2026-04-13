import io
from types import SimpleNamespace

from agent_apps.vulnhelper.app import ThinkingStreamPrinter


def _thinking_event(delta: str):
    return SimpleNamespace(
        type="message_update",
        assistant_message_event=SimpleNamespace(type="thinking_delta", delta=delta),
    )


def test_thinking_stream_printer_keeps_only_recent_character_window() -> None:
    output = io.StringIO()
    printer = ThinkingStreamPrinter(window_size=5, min_update_interval=0.0, output=output, time_fn=lambda: 0.0)

    printer.on_agent_event("planner", _thinking_event("hello"), None)
    printer.on_agent_event("planner", _thinking_event(" world"), None)
    printer.finish()

    rendered = output.getvalue()
    assert "[Thinking:planner] hello" in rendered
    assert "\r[Thinking:planner] world" in rendered
    assert rendered.endswith("\n")


def test_thinking_stream_printer_throttles_intermediate_renders_and_flushes_latest_window() -> None:
    output = io.StringIO()
    ticks = iter([0.0, 0.1, 0.6])
    printer = ThinkingStreamPrinter(window_size=12, min_update_interval=0.5, output=output, time_fn=lambda: next(ticks))

    printer.on_agent_event("planner", _thinking_event("first"), None)
    printer.on_agent_event("planner", _thinking_event(" second"), None)
    printer.finish()

    rendered = output.getvalue()
    assert rendered.count("[Thinking:planner]") == 2
    assert "first second" in rendered
    assert rendered.endswith("\n")


def test_thinking_stream_printer_starts_new_line_for_new_phase() -> None:
    output = io.StringIO()
    printer = ThinkingStreamPrinter(window_size=10, min_update_interval=0.0, output=output, time_fn=lambda: 0.0)

    printer.on_agent_event("planner", _thinking_event("plan"), None)
    printer.on_agent_event("analyst", _thinking_event("answer"), None)
    printer.finish()

    rendered = output.getvalue()
    assert "[Thinking:planner] plan" in rendered
    assert "\n[Thinking:analyst] answer" in rendered
    assert rendered.endswith("\n")


def test_thinking_stream_printer_pads_visible_window_to_fixed_width() -> None:
    output = io.StringIO()
    printer = ThinkingStreamPrinter(window_size=8, min_update_interval=0.0, output=output, time_fn=lambda: 0.0)

    printer.on_agent_event("planner", _thinking_event("hi"), None)

    rendered = output.getvalue()
    assert rendered == "[Thinking:planner] hi      "


def test_thinking_stream_printer_clears_line_on_finish() -> None:
    output = io.StringIO()
    printer = ThinkingStreamPrinter(window_size=8, min_update_interval=0.0, output=output, time_fn=lambda: 0.0)

    printer.on_agent_event("planner", _thinking_event("cleanup"), None)
    printer.finish()

    rendered = output.getvalue()
    assert rendered.endswith("\r                           \r\n")
