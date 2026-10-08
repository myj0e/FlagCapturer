"""Safe plain-text rendering for untrusted challenge and tool output."""

from __future__ import annotations

import unicodedata

from textual.widgets import Input


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
