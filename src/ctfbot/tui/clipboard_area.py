"""Text editors with system clipboard shortcuts and terminal-paste support."""
from __future__ import annotations

import threading

from textual.binding import Binding
from textual.actions import SkipAction
from textual.events import Paste
from textual.message import Message
from textual.widgets import TextArea

from ctfbot.tui.clipboard import CopyResult, copy_text, read_text


class ClipboardTextArea(TextArea):
    BINDINGS = [Binding("super+v", "paste", "粘贴", show=False)]

    class CopyFinished(Message):
        def __init__(self, result: CopyResult) -> None:
            super().__init__()
            self.result = result

    class PasteFinished(Message):
        def __init__(self, request: int, value: str | None, fallback: str,
                     document: str, selection: object) -> None:
            super().__init__()
            self.request = request
            self.value = value
            self.fallback = fallback
            self.document = document
            self.selection = selection

    _paste_request = 0
    _pending_copies = 0

    def action_copy(self) -> None:
        value = self.selected_text
        if not value:
            # Let the enclosing app keep its existing Ctrl+C behavior.
            raise SkipAction()
        self._copy_value(value)

    def action_cut(self) -> None:
        # Keep the existing cut operation compatible with system-backed Ctrl+V.
        if self.read_only:
            return
        start, end = self.selection
        result = self._delete_cursor_line() if start == end else self._delete_via_keyboard(start, end)
        if result is not None:
            self._copy_value(result.replaced_text)

    def _copy_value(self, value: str) -> None:
        try:
            self.app.copy_to_clipboard(value)
            terminal_sent = True
        except Exception:
            terminal_sent = False
        self._pending_copies += 1

        def copy() -> None:
            def terminal_result(_: str) -> None:
                if not terminal_sent:
                    raise RuntimeError("Terminal clipboard unavailable")
            try:
                result = copy_text(value, terminal_result)
            except Exception:
                result = CopyResult("系统复制失败；终端复制请求可能仍有效。", False)
            self.post_message(self.CopyFinished(result))

        threading.Thread(target=copy, name="ctfbot-copy-selection", daemon=True).start()

    def on_clipboard_text_area_copy_finished(self, event: CopyFinished) -> None:
        self._pending_copies = max(0, self._pending_copies - 1)
        self.notify(event.result.message, severity="information" if event.result.confirmed else "warning", timeout=3)

    def action_paste(self) -> None:
        if self.read_only:
            return
        self._paste_request += 1
        # A rapid Ctrl+C / Ctrl+V must not paste the previous system value while
        # the asynchronous native copy is still in flight.
        if self._pending_copies:
            self._insert_paste(self.app.clipboard)
            return
        request, document, selection = self._paste_request, self.text, self.selection
        fallback = self.app.clipboard

        def read() -> None:
            try:
                value = read_text()
            except Exception:
                value = None
            self.post_message(self.PasteFinished(request, value, fallback, document, selection))

        threading.Thread(target=read, name="ctfbot-paste", daemon=True).start()

    def on_clipboard_text_area_paste_finished(self, event: PasteFinished) -> None:
        event.stop()
        if event.request != self._paste_request or self.read_only or not self.has_focus:
            return
        if self.text != event.document or self.selection != event.selection:
            self.notify("光标或内容已改变，已取消粘贴，请重试。", timeout=3)
            return
        value = event.value
        if value is None:
            value = event.fallback
            if not value:
                self.notify("无法读取系统剪贴板，请使用终端的粘贴快捷键（如 Ctrl+Shift+V 或 Cmd+V）。", severity="warning", timeout=4)
                return
        self._insert_paste(value)

    def _insert_paste(self, value: str) -> None:
        if value and not self.read_only:
            result = self._replace_via_keyboard(value, *self.selection)
            if result is not None:
                self.move_cursor(result.end_location)

    def on_paste(self, event: Paste) -> None:
        # A terminal can deliver pasted text instead of a Ctrl+V key event.
        # Cancel any pending native read so the same paste is never inserted twice.
        event.stop()
        event.prevent_default()
        self._paste_request += 1
        self._insert_paste(event.text)
