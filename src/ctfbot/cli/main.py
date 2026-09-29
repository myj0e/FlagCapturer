'''CLI dispatch for the baseline project.'''

from __future__ import annotations

import argparse
from collections.abc import Sequence

from ctfbot import __version__
from ctfbot.application.diagnostics import collect_diagnostics, render_diagnostics
from ctfbot.tui.app import run_tui


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='ctfbot',
        description='Tool-driven CTF solving workbench for LLM agents.',
    )
    parser.add_argument(
        '--version',
        action='version',
        version=f'%(prog)s {__version__}',
    )
    subparsers = parser.add_subparsers(dest='command')
    subparsers.add_parser('doctor', help='show local project and runtime diagnostics')
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == 'doctor':
        diagnostics = collect_diagnostics()
        render_diagnostics(diagnostics)
        return 1 if any(item.status == 'FAIL' for item in diagnostics) else 0

    return run_tui()
