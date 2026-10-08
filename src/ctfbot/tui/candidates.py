"""Select and copy exact candidate strings after a run."""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Select, Static, TextArea

from ctfbot.tui.safe_text import safe_plain_text


@dataclass(frozen=True, slots=True)
class FlagCandidate:
    candidate_id: str
    value: str
    status: str


class CandidateScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "关闭")]
    DEFAULT_CSS = """
    CandidateScreen { align: center middle; background: $background 65%; }
    CandidateScreen #candidate-dialog {
        width: 90%; max-width: 100; height: auto; max-height: 90%;
        padding: 1 2; border: round $accent; background: $surface;
    }
    CandidateScreen #candidate-title { height: 2; text-style: bold; }
    CandidateScreen #candidate-select { width: 1fr; }
    CandidateScreen #candidate-value { height: 8; border: round $primary; }
    CandidateScreen #candidate-copy-status { height: auto; min-height: 2; margin-top: 1; }
    CandidateScreen #candidate-actions { height: 3; margin-top: 1; }
    """

    def __init__(self, candidates: tuple[FlagCandidate, ...]) -> None:
        super().__init__()
        self.candidates = candidates
        # Prefer a verified answer; otherwise show the most recent submission.
        self.selected_index = next((i for i in reversed(range(len(candidates)))
                                    if candidates[i].status == "verified"), len(candidates) - 1)

    def compose(self) -> ComposeResult:
        with Vertical(id="candidate-dialog"):
            yield Static("候选 flag · 选择并复制", id="candidate-title")
            if self.candidates:
                labels = {"verified": "已验证", "rejected": "验证未通过", "unverified": "未验证",
                          "format_only": "未验证（历史格式匹配）", "format_mismatch": "未验证（历史格式不匹配）"}
                yield Select([(Text(f"{safe_plain_text(c.candidate_id)} · {labels.get(c.status, safe_plain_text(c.status))} · {safe_plain_text(c.value)[:60]}"), i)
                              for i, c in enumerate(self.candidates)],
                             value=self.selected_index, allow_blank=False, id="candidate-select")
            yield TextArea(safe_plain_text(self.candidates[self.selected_index].value) if self.candidates else "",
                           read_only=True, id="candidate-value")
            yield Static("复制会保留完整原文；显示时控制字符已转义。未验证候选仍需确认。"
                         if self.candidates else "本轮没有可读取的候选 flag（未提交或证据文件不可用）。",
                         id="candidate-copy-status")
            with Horizontal(id="candidate-actions"):
                yield Button("复制 flag", variant="primary", id="copy-candidate", disabled=not self.candidates)
                yield Button("关闭", id="close-candidates")

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "candidate-select" or not isinstance(event.value, int):
            return
        self.selected_index = event.value
        self.query_one("#candidate-value", TextArea).load_text(safe_plain_text(self.candidates[event.value].value))
        self.query_one("#candidate-copy-status", Static).update("复制会保留完整原文；显示时控制字符已转义。未验证候选仍需确认。")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "close-candidates":
            self.dismiss()
        elif event.button.id == "copy-candidate" and self.candidates:
            try:
                self.app.copy_to_clipboard(self.candidates[self.selected_index].value)
            except Exception:
                self.query_one("#candidate-copy-status", Static).update("复制请求失败，请在上方文本框选择内容手动复制。")
            else:
                self.query_one("#candidate-copy-status", Static).update(
                    "已发送复制请求（完整原文）。系统剪贴板需要终端支持并允许 OSC 52；若无效，可选择上方文本手动复制。")
