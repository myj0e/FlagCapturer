"""Paged JSONL evidence and Markdown report views for local run files."""

from __future__ import annotations

import json
from itertools import islice
from pathlib import Path

from rich.syntax import Syntax
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Markdown, RichLog, Static

from ctfbot.tui.safe_text import safe_plain_text, safe_prompt_text


class RunDocumentView(Vertical):
    """Render files without replaying events into live controller/UI state."""

    PAGE_SIZE = 25
    DEFAULT_CSS = """
    RunDocumentView { height: 1fr; border: round $primary-muted; padding: 0 1; }
    RunDocumentView #document-path { height: 2; color: $text-muted; }
    RunDocumentView #document-navigation { height: 3; }
    RunDocumentView #document-page { width: 1fr; content-align: center middle; }
    RunDocumentView #evidence-content { height: 1fr; }
    RunDocumentView #report-scroll { height: 1fr; }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.path: Path | None = None
        self.page = 0

    def compose(self) -> ComposeResult:
        yield Static(id="document-path", markup=False)
        with Horizontal(id="document-navigation"):
            yield Button("上一页", id="document-previous")
            yield Static(id="document-page", markup=False)
            yield Button("下一页", id="document-next")
        yield RichLog(id="evidence-content", wrap=True, auto_scroll=False)
        with VerticalScroll(id="report-scroll"):
            yield Markdown(id="report-content", open_links=False)

    def show_evidence(self, path: Path) -> None:
        self.path = path
        self.page = 0
        self._render_page()
        self.query_one("#document-path", Static).update(safe_plain_text(path))
        self.query_one("#document-navigation").display = True
        self.query_one("#evidence-content").display = True
        self.query_one("#report-scroll").display = False
        self.border_title = "EVIDENCE / 事件原始记录"

    def _render_page(self) -> None:
        assert self.path is not None
        start = self.page * self.PAGE_SIZE
        with self.path.open(encoding="utf-8") as source:
            lines = list(islice(source, start, start + self.PAGE_SIZE + 1))
        log = self.query_one("#evidence-content", RichLog)
        log.clear()
        for number, line in enumerate(lines[:self.PAGE_SIZE], start + 1):
            log.write(Text(f"第 {number} 行", style="bold cyan"))
            try:
                formatted = json.dumps(json.loads(line), ensure_ascii=False, indent=2)
            except json.JSONDecodeError:
                log.write(Text("无效 JSON，以下保留原始内容：", style="yellow"))
                formatted = line
            log.write(Syntax(safe_prompt_text(formatted), "json", theme="ansi_dark", word_wrap=True))
        if not lines:
            log.write(Text("暂无事件记录。"))
        log.scroll_home(animate=False)
        self.query_one("#document-previous", Button).disabled = self.page == 0
        self.query_one("#document-next", Button).disabled = len(lines) <= self.PAGE_SIZE
        self.query_one("#document-page", Static).update(f"第 {self.page + 1} 页 · 每页 {self.PAGE_SIZE} 条")

    def show_report(self, path: Path, *, title: str = "REPORT / 运行报告") -> None:
        text = path.read_text(encoding="utf-8")
        self.path = path
        self.query_one("#report-content", Markdown).update(safe_prompt_text(text))
        self.query_one("#document-path", Static).update(safe_plain_text(path))
        self.query_one("#document-navigation").display = False
        self.query_one("#evidence-content").display = False
        self.query_one("#report-scroll").display = True
        self.query_one("#report-scroll").scroll_home(animate=False)
        self.border_title = title

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id not in {"document-previous", "document-next"}:
            return
        event.stop()
        previous = self.page
        self.page += 1 if event.button.id == "document-next" else -1
        try:
            self._render_page()
        except (OSError, UnicodeError) as exc:
            self.page = previous
            self.notify(f"证据读取失败：{safe_plain_text(exc)}", severity="error")
