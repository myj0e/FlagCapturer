from __future__ import annotations

import asyncio
import json
import stat

import pytest
from textual.widgets import Button, Checkbox, Input

from ctfbot.application.baseline import validate_baseline_snapshot
from ctfbot.challenge.single_file import import_single_file, select_single_attachment
from ctfbot.model_adapters.protocol import ScriptedTurn, ToolCall
from ctfbot.tui.app import CTFBotApp
from test_stage_b_tui import IMAGE, make_workspace
from test_unknown_flag import service


@pytest.mark.parametrize("authorized", [False, True])
def test_single_executable_snapshot(tmp_path, authorized):
    source = tmp_path / "reverse.elf"
    payload = b"\x7fELFsynthetic attachment; never executed on host"
    source.write_bytes(payload)
    original_mode = source.stat().st_mode
    snapshot = import_single_file(source, tmp_path / "imports", authorize_model_data=authorized)
    validate_baseline_snapshot(snapshot)
    copied = snapshot / "input" / source.name
    assert copied.read_bytes() == source.read_bytes() == payload
    assert source.stat().st_mode == original_mode
    assert stat.S_IMODE(copied.stat().st_mode) == 0o555
    provenance = json.loads((snapshot / "provenance.json").read_text())
    assert provenance["category"] == "reverse"
    assert provenance["model_data_authorized"] is authorized
    assert bool(provenance["model_data_authorization_basis"]) is authorized


def test_unsafe_filename_is_normalized(tmp_path):
    source = tmp_path / "odd file\nname"
    source.write_bytes(b"attachment")
    snapshot = import_single_file(source, tmp_path / "imports")
    assert (snapshot / "input" / "attachment.bin").read_bytes() == b"attachment"


def test_empty_symlink_and_directory_are_rejected(tmp_path):
    source = tmp_path / "empty"
    source.touch()
    link = tmp_path / "link"
    link.symlink_to(source)
    for path in (source, link, tmp_path):
        with pytest.raises((ValueError, OSError)):
            import_single_file(path, tmp_path / "imports")


@pytest.mark.parametrize("folder_input", [False, True])
def test_tui_single_file_authorization_preview_and_run(tmp_path, folder_input):
    folder = tmp_path / "RSA"
    folder.mkdir()
    source = folder / "reverse.elf"
    source.write_bytes(b"\x7fELFauthored synthetic file")
    selected = folder if folder_input else source
    async def scenario():
        app = CTFBotApp(service=service([ScriptedTurn(tool_calls=(
            ToolCall("candidate_submit", {"candidate": "flag{a}"}),))]),
            runtime_image=IMAGE, runs_root=tmp_path / "runs", imports_root=tmp_path / "imports")
        async with app.run_test(size=(120, 40)) as pilot:
            app.query_one("#workspace-path", Input).value = str(selected)
            checkbox = app.query_one("#file-model-transfer", Checkbox)
            assert checkbox._button.plain == "[ ]"
            assert "OFF" in checkbox.label.plain
            assert "状态与操作提示" in str(app.query_one("#status").border_title)
            assert "题目预览" in str(app.query_one("#preview-details").border_title)
            assert "运行记录" in str(app.query_one("#timeline").border_title)
            await pilot.click("#preview")
            await pilot.pause(.1)
            assert app._preview is not None
            assert app.query_one("#run", Button).disabled
            assert "model-selected candidate; correctness unverified" in app._preview_text
            assert not app.query("#flag-format")
            await pilot.click("#file-model-transfer")
            await pilot.pause(.1)
            assert checkbox.value and checkbox._button.plain == "[x]"
            assert "ON" in checkbox.label.plain
            assert app._preview is None
            await pilot.click("#preview")
            await pilot.pause(.1)
            snapshot = app._preview.workspace
            assert str(snapshot) != str(source)
            assert app.query_one("#workspace-path", Input).value == str(selected)
            assert str(selected) in app._preview_text and str(snapshot) in app._preview_text
            assert not app.query_one("#run", Button).disabled
            await pilot.click("#run")
            for _ in range(40):
                await pilot.pause(.1)
                if app.last_result:
                    break
            assert app.last_result and app.last_result.status == "candidate_unverified"

            assert "flag{a}" in "\n".join(app.displayed_events)
            assert source.read_bytes() == b"\x7fELFauthored synthetic file"
    asyncio.run(scenario())


def test_plain_folder_requires_exactly_one_file(tmp_path):
    with pytest.raises(ValueError, match="仅包含一个附件"):
        select_single_attachment(tmp_path)
    source = tmp_path / "binary"
    source.write_bytes(b"ELF")
    assert select_single_attachment(tmp_path) == source
    (tmp_path / "extra").write_bytes(b"more")
    with pytest.raises(ValueError, match="仅包含一个附件"):
        select_single_attachment(tmp_path)


def test_checkbox_does_not_authorize_existing_workspace(tmp_path):
    workspace = make_workspace(tmp_path, authorized=False)
    async def scenario():
        app = CTFBotApp(service=service([]), runtime_image=IMAGE,
            runs_root=tmp_path / "runs", imports_root=tmp_path / "imports")
        async with app.run_test(size=(120, 40)) as pilot:
            app.query_one("#workspace-path", Input).value = str(workspace)
            app.query_one("#file-model-transfer", Checkbox).value = True
            await pilot.pause(.1)
            await pilot.click("#preview")
            await pilot.pause(.1)
            assert app._preview and not app._preview.start_allowed
            assert app.query_one("#run", Button).disabled
    asyncio.run(scenario())
