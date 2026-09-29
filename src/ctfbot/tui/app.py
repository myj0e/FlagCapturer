'''Dependency-free navigation shell used until the TUI framework is selected.'''

from __future__ import annotations

import sys

from ctfbot import __version__
from ctfbot.application.diagnostics import collect_diagnostics, render_diagnostics


def run_tui() -> int:
    if not sys.stdin.isatty():
        print(
            'ctfbot needs an interactive terminal; use `ctfbot doctor` for diagnostics.',
            file=sys.stderr,
        )
        return 2

    while True:
        print()
        print(f'ctfbot {__version__}')
        print('CTF solving workbench — project baseline')
        print('The solve workflow is not implemented yet.')
        print()
        print('[d] Diagnostics    [q] Quit')
        try:
            choice = input('ctfbot> ').strip().lower()
        except EOFError:
            print()
            return 0

        if choice in {'q', 'quit', 'exit'}:
            return 0
        if choice in {'d', 'doctor'}:
            print()
            render_diagnostics(collect_diagnostics())
        else:
            print('Choose d for diagnostics or q to quit.')
