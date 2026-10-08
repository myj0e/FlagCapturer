from __future__ import annotations

import asyncio
import subprocess
import threading
from unittest.mock import patch

import pytest
from textual.widgets import Input

from ctfbot.application.runtime_images import RuntimeImageChoice, resolve_runtime_image
from ctfbot.application.service import LocalChallengeService
from ctfbot.tui.app import CTFBotApp


IMAGE = "sha256:" + "a" * 64
MANUAL = "sha256:" + "b" * 64
LABELS = '{"org.ctfbot.tooling.profile":"general-v2"}\n'


@pytest.mark.parametrize("stdout,code,expected", [
    (IMAGE+"\nnull\n"+LABELS, 0, IMAGE), (IMAGE+"\n{}\n"+LABELS, 0, IMAGE),
    ("ctfbot-tools:candidate\nnull\n"+LABELS, 0, ""), (IMAGE+'\n{"/data":{}}\n'+LABELS, 0, ""),
    (IMAGE+"\nnull\n{}\n", 0, ""), (IMAGE+"\nnull\nnull\n", 0, ""),
    (IMAGE+"\nnull\n", 0, ""),
    ("", 1, ""), ("invalid", 0, ""),
])
def test_local_lookup_pins_id_and_rejects_invalid_images(stdout, code, expected):
    with patch("ctfbot.application.runtime_images.subprocess.run",
               return_value=subprocess.CompletedProcess([], code, stdout, "")) as run:
        choice = resolve_runtime_image()
    assert choice.image == expected and choice.message
    args = run.call_args.args[0]
    assert args[:3] == ["docker", "image", "inspect"]
    assert args[-1] == "ctfbot-tools:candidate"
    assert "pull" not in args and "build" not in args
    assert run.call_args.kwargs["timeout"] == 3


@pytest.mark.parametrize("error", [FileNotFoundError(), subprocess.TimeoutExpired("docker", 3), OSError()])
def test_docker_unavailable_is_an_actionable_result(error):
    with patch("ctfbot.application.runtime_images.subprocess.run", side_effect=error):
        choice = resolve_runtime_image()
    assert not choice.image and "Docker" in choice.message


def app(resolver, **kwargs):
    return CTFBotApp(service=LocalChallengeService(model_factory=lambda: None, model_metadata={}),
                     runtime_resolver=resolver, **kwargs)


def test_tui_automatically_fills_local_runtime():
    async def scenario():
        tui = app(lambda: RuntimeImageChoice(IMAGE, "selected automatically"))
        async with tui.run_test() as pilot:
            await pilot.pause(.15)
            assert tui.query_one("#runtime-image", Input).value == IMAGE
            assert "automatically" in tui.status_text
            assert tui.last_result is None
    asyncio.run(scenario())


def test_tui_retains_manual_override_while_lookup_is_running():
    release = threading.Event()
    def resolver():
        release.wait(2)
        return RuntimeImageChoice(IMAGE, "automatic")
    async def scenario():
        tui = app(resolver)
        async with tui.run_test() as pilot:
            tui.query_one("#runtime-image", Input).value = MANUAL
            release.set()
            await pilot.pause(.15)
            assert tui.query_one("#runtime-image", Input).value == MANUAL
    try: asyncio.run(scenario())
    finally: release.set()


def test_tui_missing_image_can_retry_without_retyping_id():
    choices = iter([RuntimeImageChoice(message="Prepare local runtime"), RuntimeImageChoice(IMAGE, "ready")])
    async def scenario():
        tui = app(lambda: next(choices))
        async with tui.run_test() as pilot:
            await pilot.pause(.15)
            assert "Prepare local runtime" in tui.status_text
            tui.query_one("#workspace-path", Input).value = "/workspace"
            tui.query_one("#oracle-path", Input).value = "/private/oracle.json"
            assert tui._input_paths()[2] == IMAGE
            assert tui.query_one("#runtime-image", Input).value == IMAGE
    asyncio.run(scenario())


def test_explicit_image_skips_automatic_lookup():
    def resolver(): raise AssertionError("explicit selection must be preserved")
    async def scenario():
        tui = app(resolver, runtime_image=MANUAL)
        async with tui.run_test() as pilot:
            await pilot.pause(.1)
            assert tui.query_one("#runtime-image", Input).value == MANUAL
    asyncio.run(scenario())


def test_retry_during_preview_does_not_expire_the_new_preview(tmp_path):
    from test_stage_b_tui import make_workspace
    from textual.widgets import Button
    workspace, oracle = make_workspace(tmp_path)
    choices = iter([RuntimeImageChoice(message="Prepare runtime"), RuntimeImageChoice(IMAGE, "ready")])
    async def scenario():
        tui = app(lambda: next(choices), runs_root=tmp_path / "runs")
        async with tui.run_test(size=(120, 40)) as pilot:
            await pilot.pause(.15)
            tui.query_one("#workspace-path", Input).value = str(workspace)
            tui.query_one("#oracle-path", Input).value = str(oracle)
            await pilot.click("#preview")
            await pilot.pause(.15)
            assert tui._preview is not None
            assert not tui.query_one("#run", Button).disabled
            assert tui._preview.runtime_image == IMAGE
    asyncio.run(scenario())
