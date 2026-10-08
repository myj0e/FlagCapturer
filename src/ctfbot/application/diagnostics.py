'''Environment diagnostics that do not expose credentials or modify the host.'''

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TextIO

from ctfbot import __version__
from ctfbot.model_adapters.codex_app_server import find_codex_cli


@dataclass(frozen=True, slots=True)
class Diagnostic:
    name: str
    status: str
    detail: str


def collect_diagnostics() -> list[Diagnostic]:
    supported = sys.version_info >= (3, 11)
    python_version = '.'.join(str(part) for part in sys.version_info[:3])
    return [
        Diagnostic(
            name='Python',
            status='OK' if supported else 'FAIL',
            detail=f'{python_version} (requires 3.11+)',
        ),
        Diagnostic(name='ctfbot', status='OK', detail=f'version {__version__}'),
        Diagnostic(
            name='LLM provider',
            status='INFO',
            detail='Codex App Server spike is available; dynamic tools remain experimental',
        ),
        Diagnostic(
            name='Codex CLI',
            status='OK' if find_codex_cli() else 'INFO',
            detail=find_codex_cli() or 'not found on PATH; required for ChatGPT sign-in',
        ),
        Diagnostic(
            name='sandbox backend',
            status='INFO',
            detail='synthetic Docker isolation smoke passed; challenge runtime is not admitted',
        ),
        Diagnostic(
            name='TUI framework',
            status='INFO',
            detail='Textual selected for the first TUI; spike only, not a runtime dependency',
        ),
    ]


def render_diagnostics(
    diagnostics: list[Diagnostic],
    output: TextIO | None = None,
) -> None:
    stream = output if output is not None else sys.stdout
    print(f'ctfbot {__version__} — project diagnostics', file=stream)
    for item in diagnostics:
        print(f'[{item.status:4}] {item.name}: {item.detail}', file=stream)
