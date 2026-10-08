"""Run the Stage A Textual spike checks with synthetic data only."""

from __future__ import annotations

import asyncio
import os

os.environ["NO_COLOR"] = "1"

from textual.widgets import Input, RichLog

from app import TuiSpike, safe_plain_text


async def verify() -> None:
    app = TuiSpike()
    assert app.no_color, "Textual did not detect NO_COLOR"
    filter_names = {type(filter_).__name__ for filter_ in app._filters}
    assert "NoColor" in filter_names and "Monochrome" not in filter_names

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(2.6)

        assert len(app.recorded_events) >= 7
        assert app.focused is app.query_one("#fake-input", Input)
        long_event = next(
            event for event in app.recorded_events if event.startswith("06 long_output:")
        )
        assert len(long_event) > 3_800
        log = app.query_one("#events", RichLog)
        assert log.max_scroll_y > 0, "Long output did not create a scrollable log"

        unsafe_event = next(
            event for event in app.recorded_events if event.startswith("05 untrusted_text:")
        )
        assert "\x1b" not in unsafe_event and "\x07" not in unsafe_event
        assert r"\u001b" in unsafe_event
        assert r"\u0007" in unsafe_event
        assert r"\u202e" in unsafe_event

        log.scroll_to(y=0, animate=False, immediate=True)
        await pilot.pause(0.1)
        assert log.scroll_y == 0
        log.scroll_to(y=log.max_scroll_y, animate=False, immediate=True)
        await pilot.pause(0.1)
        assert log.scroll_y == log.max_scroll_y

        for width, height in ((100, 30), (52, 14), (80, 24)):
            await pilot.resize_terminal(width, height)
            await pilot.pause(0.1)
            assert app.size.width == width and app.size.height == height
            assert log.region.width > 0 and log.region.height > 0

        assert app.focused is app.query_one("#fake-input", Input)
        await pilot.press("o", "k", "enter")
        await pilot.pause(0.1)
        assert app.recorded_events[-1] == "fake_input: ok"

    escaped = safe_plain_text("line\nnext\t\x1b[2J\u202e")
    assert "\n" not in escaped and "\t" not in escaped and "\x1b" not in escaped
    assert r"\u000a" in escaped and r"\u0009" in escaped and r"\u001b" in escaped
    assert r"\u202e" in escaped
    print("TUI spike passed: NO_COLOR, focus, long-log scroll, resize, input, safe text")


if __name__ == "__main__":
    asyncio.run(verify())
