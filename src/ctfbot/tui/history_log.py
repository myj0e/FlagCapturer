"""Scrolling history stays paused until the reader explicitly resumes it."""
from textual.message import Message
from textual.widgets import RichLog


class HistoryLog(RichLog):
    class HistoryRequested(Message):
        pass

    rebuilding = False
    history_requested = False

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        # Includes keyboard navigation and dragging the scrollbar, not just wheels.
        if not self.rebuilding and new_value < old_value and new_value < self.max_scroll_y:
            self.history_requested = True
            self.post_message(self.HistoryRequested())
