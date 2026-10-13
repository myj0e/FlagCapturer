"""View incremental current-run candidates and copy exact original strings."""

from __future__ import annotations

import threading
from dataclasses import dataclass

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.message import Message
from textual.widgets import Button, Select, Static, TextArea

from ctfbot.tui.clipboard_area import ClipboardTextArea
from ctfbot.tui.clipboard import CopyResult, copy_text
from ctfbot.tui.safe_text import safe_plain_text


@dataclass(frozen=True, slots=True)
class FlagCandidate:
    candidate_id: str
    value: str
    status: str
    subtask: str = ""


class CandidateScreen(ModalScreen[None]):
    class CopyFinished(Message):
        def __init__(self, result: CopyResult) -> None:
            super().__init__()
            self.result = result

    BINDINGS = [("escape", "dismiss", "关闭")]
    DEFAULT_CSS = """
    CandidateScreen { align: center middle; background: $background 80%; }
    CandidateScreen #candidate-dialog {
        width: 90%; max-width: 100; height: 26; max-height: 90%;
        padding: 1 2; border: round $primary-muted; background: $surface;
        border-title-color: $accent;
    }
    CandidateScreen #candidate-title { height: 2; text-style: bold; color: $accent; }
    CandidateScreen #candidate-select { width: 1fr; }
    CandidateScreen #candidate-meta { height: 2; color: $text-muted; }
    CandidateScreen #candidate-live-status { height: auto; min-height: 1; color: $accent; }
    CandidateScreen #candidate-value { height: 1fr; min-height: 3; border: round $primary-muted; background: $background;
        padding: 0 1; }
    CandidateScreen #candidate-copy-status { height: auto; min-height: 2; margin-top: 1; color: $text-muted; }
    CandidateScreen #candidate-actions { height: 3; margin-top: 1; }
    """

    def __init__(self, candidates: tuple[FlagCandidate, ...], *, running: bool = False) -> None:
        super().__init__()
        self.candidates = candidates
        self.selected_index = len(candidates) - 1
        self.running = running
        self._copy_pending = False

    def compose(self) -> ComposeResult:
        with Vertical(id="candidate-dialog"):
            yield Static("候选 flag · 选择并复制", id="candidate-title")
            yield Static(self._live_text(), id="candidate-live-status")
            yield Select(self._options(), value=self.selected_index, allow_blank=False,
                         disabled=not self.candidates, id="candidate-select")
            yield Static(self._metadata(), id="candidate-meta")
            yield ClipboardTextArea(safe_plain_text(self.candidates[self.selected_index].value) if self.candidates else "",
                           read_only=True, id="candidate-value")
            yield Static("复制会保留完整原文；显示时控制字符已转义。Flag 提交即完成对应目标。"
                         if self.candidates else self._empty_text(),
                         id="candidate-copy-status")
            with Horizontal(id="candidate-actions"):
                yield Button("复制 flag", variant="primary", id="copy-candidate", disabled=not self.candidates)
                yield Button("关闭", id="close-candidates")

    def on_mount(self) -> None:
        self.query_one("#candidate-dialog").border_title = "FLAG / 候选结果"
        self.query_one("#candidate-value").border_title = "完整候选 · 只读"
        self.query_one("#candidate-value").border_subtitle = "选中后 Ctrl+C 复制"

    def _empty_text(self) -> str:
        return "尚未提交 Flag，解题继续中" if self.running else "本轮没有可读取的候选 flag（未提交或证据文件不可用）。"

    def _live_text(self) -> str:
        return f"已提交 {len(self.candidates)} 条候选 · {'解题继续中' if self.running else '运行已结束'}"

    def _options(self) -> list[tuple[Text, int]]:
        return [(Text(f"{safe_plain_text(c.candidate_id)} · {safe_plain_text(c.subtask) or '未标注子题'} · {'已提交' if c.status == 'unverified' else safe_plain_text(c.status)}"), i)
                for i, c in enumerate(self.candidates)] or [(Text("尚未提交 Flag"), -1)]

    def _metadata(self) -> Text:
        if not self.candidates:
            return Text(self._empty_text())
        candidate = self.candidates[self.selected_index]
        status = "已提交" if candidate.status == "unverified" else safe_plain_text(candidate.status)
        return Text(f"{safe_plain_text(candidate.candidate_id)} · {safe_plain_text(candidate.subtask) or '未标注子题'} · {status}")

    def update_candidates(self, candidates: tuple[FlagCandidate, ...], *, running: bool) -> None:
        """Append records without reloading the currently selected editor."""
        previous = len(self.candidates)
        self.running = running
        if candidates != self.candidates:
            self.candidates = candidates
            first = previous == 0 and bool(candidates)
            if first:
                self.selected_index = 0
            select = self.query_one("#candidate-select", Select)
            # set_options normally resets selection and queues Changed messages.
            with select.prevent(Select.Changed):
                select.set_options(self._options())
                select.value = self.selected_index
            select.disabled = not candidates
            if first:
                self.query_one("#candidate-value", TextArea).load_text(safe_plain_text(candidates[0].value))
                self.query_one("#candidate-meta", Static).update(self._metadata())
                self.query_one("#candidate-copy-status", Static).update("复制会保留完整原文；Flag 提交即完成对应目标。")
            self.query_one("#copy-candidate", Button).disabled = self._copy_pending or not candidates
        added = len(candidates) - previous
        text = self._live_text() + (f" · 新增 {added} 条候选" if added > 0 else "")
        self.query_one("#candidate-live-status", Static).update(text)
        if not candidates:
            self.query_one("#candidate-meta", Static).update(self._metadata())
            self.query_one("#candidate-copy-status", Static).update(self._empty_text())

    def on_select_changed(self, event: Select.Changed) -> None:
        if (event.select.id != "candidate-select" or not isinstance(event.value, int)
                or not 0 <= event.value < len(self.candidates) or event.value == self.selected_index):
            return
        self.selected_index = event.value
        self.query_one("#candidate-value", TextArea).load_text(safe_plain_text(self.candidates[event.value].value))
        self.query_one("#candidate-meta", Static).update(self._metadata())
        if not self._copy_pending:
            self.query_one("#candidate-copy-status", Static).update("复制会保留完整原文；显示时控制字符已转义。Flag 提交即完成对应目标。")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "close-candidates":
            self.dismiss()
        elif event.button.id == "copy-candidate" and self.candidates and not self._copy_pending:
            value = self.candidates[self.selected_index].value
            try:
                self.app.copy_to_clipboard(value)
                terminal_sent = True
            except Exception:
                terminal_sent = False
            self._copy_pending = True
            self.query_one("#copy-candidate", Button).disabled = True
            self.query_one("#candidate-copy-status", Static).update("正在复制完整原文……")

            def copy() -> None:
                def terminal_result(_: str) -> None:
                    # OSC 52 was already sent on the UI thread.
                    if not terminal_sent:
                        raise RuntimeError("Terminal clipboard unavailable")
                try:
                    result = copy_text(value, terminal_result)
                except Exception:
                    result = CopyResult("系统复制失败，请选择原文复制；终端复制请求可能仍有效。", False)
                self.post_message(self.CopyFinished(result))

            threading.Thread(target=copy, name="ctfbot-copy", daemon=True).start()

    def on_clipboard_text_area_copy_finished(self, event: ClipboardTextArea.CopyFinished) -> None:
        event.stop()
        self.query_one("#candidate-copy-status", Static).update(event.result.message)

    def on_candidate_screen_copy_finished(self, event: CopyFinished) -> None:
        self._copy_pending = False
        self.query_one("#candidate-copy-status", Static).update(event.result.message)
        self.query_one("#copy-candidate", Button).disabled = not self.candidates
