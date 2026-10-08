"""Isolated Stage A fake-event UI spike; not part of the production package."""

from __future__ import annotations

import asyncio
import os
import unicodedata

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Footer, Header, Input, RichLog, Static


def safe_plain_text(value: str) -> str:
    """Make terminal controls, line breaks, and bidi formatting visible."""
    rendered: list[str] = []
    for char in value:
        codepoint = ord(char)
        category = unicodedata.category(char)
        if category in {"Cc", "Cf", "Cs", "Zl", "Zp"}:
            if codepoint <= 0xFFFF:
                rendered.append(f"\\u{codepoint:04x}")
            else:
                rendered.append(f"\\U{codepoint:08x}")
        else:
            rendered.append(char)
    return "".join(rendered)


class TuiSpike(App[None]):
    """Show synthetic events without invoking a model, shell, or challenge."""

    TITLE = "ctfbot — Stage A TUI spike"
    BINDINGS = [("ctrl+q", "quit", "Quit")]
    CSS = """
    #workspace { height: 1fr; }
    #summary { width: 30; border: round $accent; padding: 1; }
    #events { width: 1fr; border: round $primary; }
    #fake-input { dock: bottom; height: 3; }
    """

    def __init__(self) -> None:
        # Textual's default NO_COLOR filter converts colors to grayscale. Force
        # its color-removal path so NO_COLOR disables SGR colors as expected.
        super().__init__(ansi_color=True if "NO_COLOR" in os.environ else None)
        self.recorded_events: list[str] = []

    def write_event(self, value: str) -> None:
        safe_value = safe_plain_text(value)
        self.recorded_events.append(safe_value)
        self.query_one("#events", RichLog).write(Text(safe_value))

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="workspace"):
            yield Static(
                "Run: demo\nState: solving (simulated)\nBudget: 4/30 turns\n"
                "Verifier: unverified\n\nKeys: Ctrl+Q quit",
                id="summary",
            )
            yield RichLog(id="events", wrap=True, markup=False, max_lines=2000)
        yield Input(placeholder="Fake session input; Enter records locally", id="fake-input")
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self.fake_event_stream(), group="fake-events", exclusive=True)
        self.query_one("#fake-input", Input).focus()

    async def fake_event_stream(self) -> None:
        samples = [
            "01 run_started: challenge=demo; network=disabled",
            "02 model_message: classify as crypto; confidence=0.62",
            "03 tool_call: file.list(path='input')",
            "04 tool_result: 2 entries; artifact=artifacts/listing.txt",
            "05 untrusted_text: ESC=\x1b[2J OSC=\x1b]52;c;clipboard BEL=\x07 "
            "BIDI=\u202e",
            "06 long_output: " + "0123456789abcdef" * 240,
        ]
        for sample in samples:
            await asyncio.sleep(0.35)
            self.write_event(sample)
        self.write_event("07 stream_complete: synthetic events only")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.value:
            self.write_event(f"fake_input: {event.value}")
            event.input.value = ""


if __name__ == "__main__":
    TuiSpike().run()
