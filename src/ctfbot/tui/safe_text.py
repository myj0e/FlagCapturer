"""Safe plain-text rendering for untrusted challenge and tool output."""

from __future__ import annotations

import unicodedata

from textual.widgets import Input
from textual.document._document import EditResult, Location

from ctfbot.tui.clipboard_area import ClipboardTextArea


def safe_plain_text(value: object) -> str:
    """Make terminal controls, line separators, and bidirectional formatting visible."""
    rendered: list[str] = []
    for char in str(value):
        codepoint = ord(char)
        if unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"}:
            if codepoint <= 0xFFFF:
                rendered.append(f"\\u{codepoint:04x}")
            else:
                rendered.append(f"\\U{codepoint:08x}")
        else:
            rendered.append(char)
    return "".join(rendered)


class SafePathInput(Input):
    """Keep terminal controls and bidi formatting visible in editable path fields."""

    def _watch_value(self, value: str) -> None:
        safe_value = safe_plain_text(value)
        if safe_value != value:
            # Store the visible escaped form so neither Textual's renderer nor its
            # Changed message ever receives the original control characters.
            self.value = safe_value
            return
        super()._watch_value(value)


def safe_prompt_text(value: str) -> str:
    """Preserve editable lines and indentation, escaping other terminal controls."""
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(char if char in "\n\t" else safe_plain_text(char) for char in value)


class SafePromptArea(ClipboardTextArea):
    """Multiline hints with the same control-character handling as path inputs."""

    def _set_document(self, text: str, language: str | None) -> None:
        super()._set_document(safe_prompt_text(text), language)

    def insert(self, text: str, location: Location | None = None, *,
               maintain_selection_offset: bool = True) -> EditResult:
        return super().insert(safe_prompt_text(text), location,
                              maintain_selection_offset=maintain_selection_offset)

    def replace(self, insert: str, start: Location, end: Location, *,
                maintain_selection_offset: bool = True) -> EditResult:
        return super().replace(safe_prompt_text(insert), start, end,
                               maintain_selection_offset=maintain_selection_offset)
