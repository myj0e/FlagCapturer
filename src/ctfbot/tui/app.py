"""Textual single-run workbench for the Stage B local attachment workflow."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import stat
import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, HorizontalScroll, Vertical, VerticalScroll
from textual.content import Content
from textual.theme import Theme
from textual.events import MouseScrollUp, Resize
from textual.widgets import Button, Checkbox, Footer, Header, Input, Label, RichLog, Static, TextArea

from ctfbot.agent.loop import RunLimits, RunResult
from ctfbot.application.control import RunControl
from ctfbot.application.runtime_images import RuntimeImageChoice, resolve_runtime_image
from ctfbot.application.service import ChallengePreview, LocalChallengeService, create_codex_application_service
from ctfbot.challenge.single_file import import_single_file, select_single_attachment
from ctfbot.reporting import generate_basic_report
from ctfbot.tui.documents import RunDocumentView
from ctfbot.tui.context_status import ContextStatus
from ctfbot.tui.history_log import HistoryLog
from ctfbot.tui.candidates import CandidateScreen, FlagCandidate
from ctfbot.tui.safe_text import SafePathInput, SafePromptArea, safe_plain_text
from ctfbot.tools.reliability import SUMMARY_SCHEMA
from ctfbot.tools.schema import validate
from ctfbot.tui.summary import SolvingSummary
from ctfbot.tui.timeline import TimelineState, compact_event, render_timeline


class FileTransferCheckbox(Checkbox):
    """Use visible text and ASCII marks, including in terminals without color."""

    @property
    def _button(self) -> Content:
        return Content("[x]" if self.value else "[ ]")


class CTFBotApp(App[None]):
    """Preview, run, stop, inspect, and report one admitted local challenge."""

    TITLE = "FlagCapturer"
    SUB_TITLE = "CTF solving workbench"
    BINDINGS = [("ctrl+q", "request_quit", "退出"), ("f2", "toggle_layout", "配置 / 记录"), ("f3", "toggle_summary", "摘要")]
    CSS = """
    Screen { background: $background; color: $foreground; }
    Header { background: $surface; color: $foreground; }
    HeaderTitle { text-style: bold; }
    Footer { background: $surface; }
    #body { height: 1fr; layout: horizontal; padding: 1; }
    #configuration {
        width: 44; height: 1fr; margin-right: 1; padding: 0 1;
        border: round $primary-muted; background: $surface;
        border-title-color: $accent; border-title-style: bold;
    }
    #fields { height: auto; }
    .form-row { height: 4; layout: vertical; }
    .form-label { height: 1; width: 1fr; color: $text-muted; }
    Input { width: 1fr; height: 3; border: tall $panel; background: $background; padding: 0 1; }
    Input:focus { border: tall $accent; }
    #prompt-row { height: 7; }
    #additional-prompt {
        width: 1fr; height: 6; padding: 0 1; border: round $panel;
        background: $background; border-subtitle-color: $text-muted;
    }
    #additional-prompt:focus { border: round $accent; }
    #additional-prompt .text-area--selection { background: $primary 30%; }
    #additional-prompt .text-area--cursor { background: $accent; color: $background; }
    Checkbox { width: 1fr; height: 3; padding: 0; border: none; background: $surface; }
    Checkbox:focus { background: $panel; }
    #preview-details {
        height: 1fr; min-height: 6; padding: 0 1; margin-top: 1;
        border-top: solid $primary-muted; overflow-y: auto; color: $text-muted;
    }
    #workbench { width: 1fr; height: 1fr; }
    #actions { height: 3; overflow-y: hidden; }
    Button { margin-right: 1; min-width: 8; height: 3; border: tall $panel; background: $panel; }
    Button:hover { background: $primary-muted; }
    Button:focus { text-style: bold; border: tall $accent; }
    Button.-primary { background: $primary; color: $background; border: tall $primary; }
    Button.-success { background: $success; color: $background; border: tall $success; }
    Button.-error { background: $surface; color: $error; border: tall $surface; }
    Button:disabled { background: $surface; color: $text-disabled; border: tall $surface; }
    #status {
        height: 3; padding: 0 1; margin-top: 1; border-left: thick $accent;
        background: $surface; content-align: left middle; overflow-y: auto;
    }
    #metrics { height: 1; margin: 1 0; color: $text-muted; }
    #context-status { height: 1; padding: 0 1; background: $surface; color: $text-muted; }
    #run-panels { height: 1fr; }
    #run-document { display: none; }
    #timeline-panel { width: 3fr; height: 1fr; }
    #summary-panel {
        display: none; width: 1fr; min-width: 28; height: 1fr; margin-left: 1;
        border: round $primary-muted; padding: 0 1; background: $surface;
        border-title-color: $accent;
    }
    #solve-summary { height: auto; }
    Screen.run-view #summary-panel { display: block; }
    Screen.summary-hidden #summary-panel { display: none; }
    #run-panels.narrow { layout: vertical; }
    #run-panels.narrow #timeline-panel { width: 1fr; height: 3fr; }
    #run-panels.narrow #summary-panel { width: 1fr; height: 1fr; min-height: 6; margin: 1 0 0 0; }
    #timeline {
        height: 1fr; min-height: 4; border: round $primary-muted; padding: 0 1;
        background: $surface; border-title-color: $accent; border-title-style: bold;
    }
    #timeline:focus { border: round $accent; }
    #follow-timeline { display: none; min-width: 25; }
    #follow-timeline.visible { display: block; }
    Screen.run-view #configuration, Screen.run-view #fields { display: none; }
    Screen.compact #body { layout: vertical; padding: 0 1; }
    Screen.compact #configuration { width: 1fr; height: 10; margin: 0; border: none; padding: 0; }
    Screen.compact.tall #configuration { height: 16; }
    Screen.compact #fields { height: auto; }
    Screen.compact .form-row { height: 2; layout: horizontal; }
    Screen.compact .form-label { width: 24; height: 2; content-align: left middle; }
    Screen.compact Input { height: 2; border: none; }
    Screen.compact Checkbox { height: 2; }
    Screen.compact #prompt-row { height: 4; }
    Screen.compact #additional-prompt { height: 4; }
    Screen.compact #preview-details { height: 4; min-height: 4; margin: 0; }
    Screen.compact #status { margin-top: 0; height: 3; }
    Screen.compact #metrics { margin: 0; }
    """

    def __init__(
        self,
        *,
        service: LocalChallengeService | None = None,
        service_factory: Callable[[], LocalChallengeService] | None = None,
        runtime_image: str = "",
        runs_root: Path = Path("runs/stage-b"),
        limits: RunLimits = RunLimits(),
        runtime_resolver: Callable[[], RuntimeImageChoice] = resolve_runtime_image,
        additional_prompt: str = "",
        imports_root: Path | None = None,
    ) -> None:
        super().__init__(ansi_color=True if "NO_COLOR" in os.environ else None)
        self.register_theme(Theme(
            name="flagcapturer", primary="#7dcfff", secondary="#bb9af7", accent="#7dcfff",
            foreground="#c0caf5", background="#16161e", surface="#1a1b26", panel="#24283b",
            success="#9ece6a", warning="#e0af68", error="#f7768e", dark=True,
        ))
        self.theme = "flagcapturer"
        self.service = service or (service_factory or create_codex_application_service)()
        self.runtime_image = runtime_image
        self._runtime_resolver = runtime_resolver
        self._runtime_lookup_active = False
        self.runs_root = runs_root
        self.limits = limits
        self.additional_prompt = additional_prompt
        self.imports_root = imports_root or Path.cwd() / "data" / "imports"
        self._preview: ChallengePreview | None = None
        self._control: RunControl | None = None
        self._event_queue: queue.SimpleQueue[tuple[str | tuple[int, str], Any]] = queue.SimpleQueue()
        self._worker_thread: threading.Thread | None = None
        self._run_active = False
        self._quit_after_run = False
        self.last_result: RunResult | None = None
        self.last_report_path: Path | None = None
        self.candidate_status: str | None = None
        self._candidates: dict[str, FlagCandidate] = {}
        self._run_evidence_dir: Path | None = None
        self._run_generation = 0
        self.displayed_events: list[str] = []
        self._timeline_state = TimelineState()
        self._context_status = ContextStatus()
        self._timeline_dirty = False
        self._follow_timeline = True
        self._pending_timeline_events = 0
        self._run_started_at: float | None = None
        self._run_elapsed_seconds = 0
        self._run_id = ""
        self._turn_count = 0
        self._tool_count = 0
        self._turn_has_message = False
        self._solving_summary = SolvingSummary()
        self._preview_text = ""
        self.status_text = "Select a challenge file or imported workspace, then Preview before running."

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            with VerticalScroll(id="configuration"):
                with Vertical(id="fields"):
                    with Horizontal(classes="form-row"):
                        yield Label("Challenge file / folder", classes="form-label")
                        yield SafePathInput(placeholder="Single executable/attachment, or imported workspace directory", id="workspace-path")
                    with Horizontal(classes="form-row"):
                        yield Label("File model transfer", classes="form-label")
                        yield FileTransferCheckbox("未允许 / OFF · 不发送附件与输出", id="file-model-transfer", tooltip="勾选允许该题附件与工具输出发送给配置的模型；点击可撤销。已有 workspace 的授权由其记录决定。")
                    with Horizontal(classes="form-row", id="prompt-row"):
                        yield Label("补充提示词（可选）", classes="form-label")
                        yield SafePromptArea(self.additional_prompt, placeholder="解题线索、已知 flag 格式或其他提示…",
                                             id="additional-prompt", soft_wrap=True, tab_behavior="focus",
                                             highlight_cursor_line=False)
                    with Horizontal(classes="form-row"):
                        yield Label("Runtime (automatic)", classes="form-label")
                        yield SafePathInput(value=self.runtime_image,
                                            placeholder="Selected automatically; optional pinned image override",
                                            id="runtime-image")
                    with Horizontal(classes="form-row"):
                        yield Label("Private run output", classes="form-label")
                        yield SafePathInput(value=str(self.runs_root), id="runs-root")
                yield Static(Text("选择题目附件，检查预览后开始解题。\n模型传输许可由你控制；运行证据保存在本地。"), id="preview-details")
            with Vertical(id="workbench"):
                with HorizontalScroll(id="actions"):
                    yield Button("Preview", id="preview", variant="primary")
                    yield Button("Run", id="run", disabled=True, variant="success")
                    yield Button("Stop", id="stop", disabled=True, variant="error")
                    yield Button("Log", id="toggle-layout", tooltip="F2：切换配置与大记录视图")
                    yield Button("Evidence", id="evidence", disabled=True)
                    yield Button("Report", id="report", disabled=True)
                    yield Button("Writeup", id="writeup", disabled=True, tooltip="查看 agent 生成并保存的解题总结")
                    yield Button("Flag", id="candidates", disabled=True, tooltip="运行中即可查看、选择和复制已提交的候选 Flag")
                    yield Button("Quit", id="quit", tooltip="Ctrl+Q：退出")
                yield Static(self._as_text(self.status_text), id="status")
                yield Static(Text("IDLE  ·  轮次 0  ·  工具 0  ·  00:00"), id="metrics")
                yield RunDocumentView(id="run-document")
                with Horizontal(id="run-panels"):
                    with Vertical(id="timeline-panel"):
                        yield Button("查看最新", id="follow-timeline", tooltip="恢复自动跟随最新记录")
                        yield HistoryLog(id="timeline", wrap=True, markup=False, max_lines=2000, auto_scroll=False)
                    with VerticalScroll(id="summary-panel"):
                        yield Static(self._solving_summary.render(), id="solve-summary")
        yield Static(self._context_status.render(), id="context-status")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#additional-prompt", TextArea).border_subtitle = "Enter 换行 · ^C / ^V"
        self.query_one("#additional-prompt").tooltip = "Enter 换行；Tab 下一项；选中后 Ctrl+C 复制；Ctrl+V 粘贴系统剪贴板。终端原生粘贴也可使用。"
        self.query_one("#context-status").tooltip = "Token 为本次运行累计用量（输入/输出）；上下文≈ 为最近推理的窗口占用估计。K=1,000，M=1,000,000。用量仅供参考，可随时 Stop。"
        self._adapt_layout(self.size.width, self.size.height)
        self.query_one("#configuration").border_title = "CHALLENGE / 题目配置"
        self.query_one("#status", Static).border_title = "状态与操作提示"
        self.query_one("#preview-details", Static).border_title = "题目预览"
        self.query_one("#timeline", RichLog).border_title = "ACTIVITY / 运行记录"
        self.query_one("#summary-panel").border_title = "INSIGHTS / 解题摘要"
        self.query_one("#timeline", RichLog).write(render_timeline(self._timeline_state))
        self.set_interval(0.05, self._drain_events)
        self.set_interval(1, self._refresh_run_status)
        self.query_one("#workspace-path", Input).focus()
        if not self.runtime_image:
            self._runtime_lookup_active = True
            self._set_status("Finding the local runtime image…")
            def resolve() -> None:
                self._event_queue.put(("runtime_image", self._runtime_resolver()))
            threading.Thread(target=resolve, name="ctfbot-runtime-image", daemon=True).start()

    def on_unmount(self) -> None:
        if self._run_active and self._control is not None:
            self._control.cancel()
            if self._worker_thread is not None:
                self._worker_thread.join(timeout=15)

    def on_input_changed(self, event: Input.Changed) -> None:
        if self._preview is None or self._run_active:
            return
        if event.input.id == "runtime-image" and event.value.strip() == self._preview.runtime_image:
            # Automatic resolution during Preview posts a delayed Changed
            # event. That value already belongs to the new preview.
            return
        self._expire_preview()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id != "additional-prompt" or self._preview is None or self._run_active:
            return
        if event.text_area.text != self._preview.additional_prompt:
            self._expire_preview()

    def _expire_preview(self) -> None:
        self._preview = None
        self.query_one("#run", Button).disabled = True
        self._set_preview("Inputs changed. Preview again before starting a run.")
        self._set_status("Preview expired because an input changed.")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        action = event.button.id
        if action == "preview":
            self._preview_challenge()
        elif action == "run":
            self._start_run()
        elif action == "stop":
            self.action_stop()
        elif action == "evidence":
            self._show_evidence()
        elif action == "candidates":
            self._show_candidates()
        elif action == "report":
            self._write_report()
        elif action == "writeup":
            self._show_writeup()
        elif action == "quit":
            self.action_request_quit()
        elif action == "toggle-layout":
            self.action_toggle_layout()
        elif action == "follow-timeline":
            self._resume_timeline_follow()

    def on_resize(self, event: Resize) -> None:
        if self.is_mounted:
            self._adapt_layout(event.size.width, event.size.height)

    def _adapt_layout(self, width: int, height: int) -> None:
        workbench_screen = self.query_one("#body").screen
        workbench_screen.set_class(width < 120, "compact")
        workbench_screen.set_class(height >= 30, "tall")
        self.query_one("#run-panels").set_class(width < 110, "narrow")

    def action_toggle_summary(self) -> None:
        self.default_screen.toggle_class("summary-hidden")

    def _refresh_summary(self) -> None:
        self.query_one("#solve-summary", Static).update(self._solving_summary.render())

    def _reset_summary(self) -> None:
        self._solving_summary = SolvingSummary()
        self._refresh_summary()

    def on_mouse_scroll_up(self, event: MouseScrollUp) -> None:
        if getattr(event.widget, "id", None) == "timeline":
            self._pause_timeline_follow()

    def on_history_log_history_requested(self, event: HistoryLog.HistoryRequested) -> None:
        if self.query_one("#timeline", HistoryLog).history_requested:
            self._pause_timeline_follow()

    def _refresh_context_status(self) -> None:
        self.query_one("#context-status", Static).update(self._as_text(self._context_status.render()))

    def action_toggle_layout(self) -> None:
        if self.query_one("#run-document").display:
            self._select_run_view("activity")
            self._expand_log()
            return
        self.default_screen.toggle_class("run-view")
        self.query_one("#toggle-layout", Button).label = "Config" if self.default_screen.has_class("run-view") else "Log"

    def _expand_log(self) -> None:
        self._select_run_view("activity")
        self.default_screen.add_class("run-view")
        self.query_one("#toggle-layout", Button).label = "Config"
        self.query_one("#timeline", RichLog).focus()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id != "file-model-transfer":
            return
        event.checkbox.label = ("已允许 / ON · 附件与输出可发送"
                                if event.value else "未允许 / OFF · 不发送附件与输出")
        if self._preview is not None and not self._run_active:
            self._preview = None
            self.query_one("#run", Button).disabled = True
            self._set_preview("传输许可已改变，旧预览失效。点击 Preview，重新生成并检查题目快照。")
        if not self._run_active:
            self._set_status("已允许模型传输。路径保持为原始输入；点击 Preview 导入并检查题目，再 Run。"
                             if event.value else "未允许模型传输。可 Preview 查看附件；运行单文件题目前需勾选许可。")

    def action_stop(self) -> None:
        if not self._run_active or self._control is None:
            return
        self._control.cancel()
        self._set_status("Stop requested. Waiting for model and sandbox cleanup…")

    def action_request_quit(self) -> None:
        if self._run_active:
            self._quit_after_run = True
            self.action_stop()
            self._set_status("Stopping the active run before exit…")
            return
        self.exit()

    def _preview_challenge(self) -> None:
        if self._run_active:
            return
        self._select_run_view("activity")
        self.default_screen.remove_class("run-view")
        self.query_one("#toggle-layout", Button).label = "Log"
        self.query_one("#run", Button).disabled = True
        self.query_one("#evidence", Button).disabled = True
        self.query_one("#report", Button).disabled = True
        self.query_one("#writeup", Button).disabled = True
        try:
            workspace, image, runs_root = self._input_paths()
            attachment = select_single_attachment(workspace)
            if attachment is not None:
                if self.imports_root.resolve().is_relative_to(runs_root.resolve()) or runs_root.resolve().is_relative_to(self.imports_root.resolve()):
                    raise ValueError("challenge imports and run output directories must be disjoint")
                workspace = import_single_file(attachment, self.imports_root,
                    authorize_model_data=self.query_one("#file-model-transfer", Checkbox).value)
            preview = self.service.preview(workspace, image, runs_root, self.limits,
                                           additional_prompt=self.query_one("#additional-prompt", TextArea).text)
        except Exception as exc:
            self._preview = None
            self._set_preview("Preview failed / 预览失败：" + safe_plain_text(str(exc)) +
                              "\n可选择单个附件、仅含一个附件的普通目录，或已导入的 workspace。")
            self._set_status(f"Preview error: {safe_plain_text(str(exc))}")
            return
        self._preview = preview
        self.candidate_status = None
        self.last_result = None
        self._candidates.clear()
        self._run_evidence_dir = None
        self._run_id = ""
        self._run_generation += 1
        self._sync_candidates()
        self.last_report_path = None
        self.displayed_events.clear()
        self._turn_count = 0
        self._tool_count = 0
        self._run_id = ""
        self._run_started_at = None
        self._run_elapsed_seconds = 0
        self._reset_summary()
        self._timeline_state.clear()
        self._context_status = ContextStatus()
        self._refresh_context_status()
        self._timeline_dirty = False
        self._follow_timeline = True
        self.query_one("#timeline", HistoryLog).history_requested = False
        self._pending_timeline_events = 0
        self.query_one("#follow-timeline", Button).remove_class("visible")
        self.query_one("#timeline", RichLog).clear()
        self._set_preview(self._preview_lines(preview))
        self.query_one("#run", Button).disabled = not preview.start_allowed
        if preview.start_block_reason:
            self._set_status(safe_plain_text(preview.start_block_reason))
        elif preview.start_allowed:
            self._set_status("预览成功，" + ("已导入附件；原始路径保持不变。" if attachment is not None else "题目快照已检查。") + "点击 Run 开始解题。")
        else:
            self._set_status("预览成功，尚未允许模型传输。" +
                             ("勾选 File model transfer 后再次 Preview。" if attachment is not None
                              else "请检查已有 workspace 中记录的题目授权；勾选框不会修改它。"))

    def _preview_lines(self, preview: ChallengePreview) -> str:
        lines = [
            "Run: " + ("READY — 点击 Run 开始" if preview.start_allowed else "BLOCKED — 查看下方授权及状态提示"),
            f"原始路径（保留不变）: {safe_plain_text(self.query_one('#workspace-path', Input).value)}",
            f"Snapshot: {safe_plain_text(preview.workspace)} — 实际运行使用的题目快照",
            f"Challenge: {safe_plain_text(preview.challenge_id)}",
            f"Category: {safe_plain_text(preview.category or 'unspecified')} | import mode: {safe_plain_text(preview.import_mode)}",
            f"Model transfer: {'AUTHORIZED' if preview.model_data_authorized else 'NOT AUTHORIZED'}",
            f"Authorization basis: {safe_plain_text(preview.authorization_basis or 'none recorded')}",
            f"Verifier: {safe_plain_text(preview.verifier)}",
            f"Runtime: {safe_plain_text(preview.runtime_profile)}",
            f"Image: {safe_plain_text(preview.runtime_image)}",
            "Single-tool protections (no task quota): " + ", ".join(
                f"{key}={safe_plain_text(value)}"
                for key, value in asdict(preview.limits).items()
            ),
            "Inputs:",
        ]
        if preview.service_endpoint:
            lines.insert(-1, f"Authorized endpoint: {safe_plain_text(preview.service_endpoint)}")
        if preview.additional_prompt.strip():
            lines.insert(-1, "补充提示词:\n" + "\n".join("  " + safe_plain_text(line) for line in preview.additional_prompt.split("\n")))
        if preview.memory_policy:
            policy = preview.memory_policy
            lines.insert(-1, f"Memory: {safe_plain_text(policy['mode'])}; namespaces {safe_plain_text(', '.join(policy['namespaces']) or 'none')}; {len(policy['versions'])} reviewed versions")
        if preview.start_block_reason:
            lines.insert(-1, f"Run blocked: {safe_plain_text(preview.start_block_reason)}")
        lines.extend(
            f"  {safe_plain_text(item.path)} — {item.bytes} bytes — SHA-256 {item.sha256}"
            for item in preview.inputs
        )
        return "\n".join(lines)

    def _start_run(self) -> None:
        if self._run_active:
            return
        if self._preview is None:
            self._set_status("Preview this workspace before starting a run.")
            return
        if not self._preview.start_allowed:
            self._set_status(safe_plain_text(self._preview.start_block_reason) if self._preview.start_block_reason else
                             "Run blocked: this challenge is not authorized for model transfer.")
            return
        try:
            workspace, image, runs_root = self._input_paths()
        except ValueError as exc:
            self._set_status(f"Run blocked: {safe_plain_text(str(exc))}")
            return
        workspace = Path(self._preview.workspace)

        self._run_active = True
        self._context_status = ContextStatus()
        self._refresh_context_status()
        self._reset_summary()
        self._expand_log()
        self._control = RunControl()
        self.candidate_status = None
        self.last_result = None
        self._candidates.clear()
        self._run_evidence_dir = None
        self._run_id = ""
        self._run_generation += 1
        self._sync_candidates()
        self.last_report_path = None
        self._turn_count = 0
        self._tool_count = 0
        self.query_one("#preview", Button).disabled = True
        self.query_one("#run", Button).disabled = True
        self.query_one("#stop", Button).disabled = False
        self.query_one("#evidence", Button).disabled = True
        self.query_one("#report", Button).disabled = True
        self.query_one("#writeup", Button).disabled = True
        self._set_status("Run starting. Model and runtime will be created after preflight succeeds.")
        control = self._control
        additional_prompt = self._preview.additional_prompt
        generation = self._run_generation

        def work() -> None:
            evidence_dir: Path | None = None

            def publish_event(record: dict[str, Any]) -> None:
                nonlocal evidence_dir
                run_id = record.get("run_id")
                if isinstance(run_id, str):
                    try:
                        canonical_run_id = str(uuid.UUID(run_id))
                    except ValueError:
                        pass
                    else:
                        evidence_dir = Path(runs_root).absolute() / canonical_run_id
                self._event_queue.put(((generation, "event"), (record, evidence_dir)))

            try:
                result = self.service.run(
                    workspace,
                    image,
                    runs_root,
                    self.limits,
                    control=control,
                    event_sink=publish_event,
                    additional_prompt=additional_prompt,
                )
            except Exception as exc:
                self._event_queue.put(((generation, "error"), exc))
            else:
                self._event_queue.put(((generation, "result"), result))

        self._worker_thread = threading.Thread(
            target=work,
            name="ctfbot-single-challenge-run",
            daemon=True,
        )
        self._worker_thread.start()

    def _drain_events(self) -> None:
        while True:
            try:
                kind, payload = self._event_queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(kind, tuple):
                generation, kind = kind
                if generation != self._run_generation:
                    continue
            if kind == "runtime_image":
                self._runtime_lookup_active = False
                field = self.query_one("#runtime-image", Input)
                if not field.value.strip() and not self._run_active:
                    if payload.image:
                        field.value = payload.image
                    if self._preview is None:
                        self._set_status(payload.message)
            elif kind == "event":
                record, evidence_dir = payload
                self._render_event(record, evidence_dir=evidence_dir)
            elif kind == "result":
                self._finish_run(payload)
            elif kind == "error":
                self._finish_error(payload)
        if self._timeline_dirty:
            self._timeline_dirty = False
            self._refresh_timeline()

    def _freeze_elapsed(self) -> None:
        if self._run_started_at is not None:
            self._run_elapsed_seconds = int(time.monotonic() - self._run_started_at)

    def _finish_run(self, result: RunResult) -> None:
        self._freeze_elapsed()
        self.last_result = result
        self._run_id = result.run_id
        self._run_evidence_dir = Path(result.run_dir)
        self._run_active = False
        self._control = None
        if self.last_result is not None:
            self._context_status.update_usage(self.last_result.usage_totals)
            self._refresh_context_status()
        self.query_one("#preview", Button).disabled = False
        self.query_one("#stop", Button).disabled = True
        self.query_one("#evidence", Button).disabled = False
        self.query_one("#report", Button).disabled = False
        self._sync_candidates()
        self.query_one("#run", Button).disabled = True
        self.query_one("#writeup", Button).disabled = not (Path(result.run_dir) / "writeup.md").is_file()
        if result.status in {"completed", "candidate_unverified"}:
            message = f"任务已完成，Flag 已提交，无需等待平台验证。记录：{result.run_dir}"
        elif result.status == "partial":
            message = f"已提交部分 Flag，仍有实际子任务未完成。记录：{result.run_dir}"
        elif result.status == "unsolved":
            message = f"本次尝试未解出，未声称题目无解。Evidence: {result.run_dir}"
        elif result.status == "user_cancelled":
            message = f"Run stopped by user. Evidence preserved at: {result.run_dir}"
        elif result.status == "error":
            message = f"Run ended with an environment error. Evidence: {result.run_dir}"
        else:
            candidate = self.candidate_status or "none submitted"
            message = f"Run {result.status} ({result.stop_reason}); candidate: {candidate}. Evidence: {result.run_dir}"
        self._solving_summary.outcome = result.status
        self._refresh_summary()
        self._set_status(message + " · 点击 Flag 选择候选并复制。")
        if self._quit_after_run:
            self.exit()

    def _finish_error(self, error: Exception) -> None:
        self._freeze_elapsed()
        self._run_active = False
        self._control = None
        self.query_one("#preview", Button).disabled = False
        self.query_one("#stop", Button).disabled = True
        self.query_one("#run", Button).disabled = self._preview is None or not self._preview.start_allowed
        self.query_one("#evidence", Button).disabled = self.last_result is None
        self.query_one("#report", Button).disabled = self.last_result is None
        self.query_one("#writeup", Button).disabled = self.last_result is None or not (Path(self.last_result.run_dir) / "writeup.md").is_file()
        self._sync_candidates()
        self._solving_summary.outcome = "运行错误"
        self._refresh_summary()
        error_type = safe_plain_text(type(error).__name__)
        self._render_event({"event_type": "ui_error", "error_type": error_type})
        self._set_status(f"Run error ({error_type}). Check the workspace, model setup, and runtime.")
        if self._quit_after_run:
            self.exit()

    def _render_event(self, event: dict[str, Any], *, evidence_dir: Path | None = None) -> None:
        event_type = event.get("event_type", "unknown")
        event_run_id = event.get("run_id")
        if isinstance(event_run_id, str):
            if self._run_id and event_run_id != self._run_id:
                return  # Ignore late events from another run, including candidates.
            self._run_id = event_run_id
            if evidence_dir is not None:
                self._run_evidence_dir = evidence_dir
        if event_type == "run_started":
            self._run_id = str(event.get("run_id", ""))
            if self._run_active:
                self._run_started_at = time.monotonic()
        elif event_type == "model_turn_started":
            self._turn_count = int(event.get("turn", self._turn_count + 1))
            self._turn_has_message = False
        elif event_type == "assistant_message":
            self._turn_has_message = True
        elif event_type == "tool_call":
            self._tool_count = int(event.get("tool_index", self._tool_count + 1))
        elif event_type == "tool_result" and event.get("name") == "candidate_submit":
            result = event.get("result")
            if isinstance(result, dict) and result.get("status") == "unverified":
                self.candidate_status = "unverified"

        if event_type in {"provider_initialized", "run_metadata"} and isinstance(event.get("provider"), dict):
            metadata = event["provider"]
            self._context_status.provider = str(metadata.get("provider") or "")
            self._context_status.model = str(metadata.get("model") or "")
            self._refresh_context_status()
        elif event_type == "provider_context_usage":
            self._context_status.update(event)
            self._refresh_context_status()
        elif event_type == "context_compaction_completed":
            self._context_status.update({"usage": event.get("used"), "capacity": event.get("capacity"), "estimated": True})
            self._refresh_context_status()
        elif event_type in {"provider_compaction_started", "provider_compaction_completed", "context_compaction_started"}:
            self._context_status.used = None
            self._context_status.compacting = event_type in {"provider_compaction_started", "context_compaction_started"}
            self._refresh_context_status()
        elif event_type == "model_turn_completed":
            self._context_status.update_usage(event.get("usage_totals"))
            self._refresh_context_status()
        elif event_type == "run_finished":
            if isinstance(event.get("provider_context"), dict):
                self._context_status.update(event["provider_context"])
            self._context_status.update_usage(event.get("provider_billing_usage"), provider=True)
            self._context_status.update_usage(event.get("usage_totals"))
            self._refresh_context_status()

        reader = lambda ref: self._read_artifact(ref, evidence_dir, limit=256 * 1024)
        candidate_value = (self._read_candidate_value(event, evidence_dir)
                           if event_type == "tool_result" and event.get("name") == "candidate_submit" else None)
        if candidate_value is not None:
            self._record_candidate(event, candidate_value)
        if event_type == "assistant_message" or (
            event_type == "tool_result" and event.get("name") not in {"summary_update", "claim_record", "experiment_record", "run_complete"}
        ):
            self._solving_summary.pending_progress = True
        if event_type == "model_turn_started":
            self._solving_summary.turn = self._turn_count
        elif event_type == "summary_updated":
            raw = reader(event.get("details"))
            try:
                snapshot = json.loads(raw) if raw is not None else None
                # Validate stored snapshots before rendering historical evidence.
                if snapshot is not None:
                    validate(snapshot, SUMMARY_SCHEMA)
                self._solving_summary.replace(snapshot, str(event.get("timestamp_utc", "")))
            except (ValueError, TypeError):
                self._solving_summary.read_error = True
        elif candidate_value is not None:
            self._solving_summary.candidate = candidate_value
        elif event_type == "run_finished":
            self._solving_summary.outcome = str(event.get("status", "已结束"))
        if event_type in {"model_turn_started", "assistant_message", "tool_result", "summary_updated", "run_finished"}:
            self._refresh_summary()
        self._timeline_state.add(event, reader, candidate_value)
        if event_type in {"model_turn_started", "model_turn_completed", "assistant_message", "tool_call",
                          "tool_result", "completion_recorded", "sandbox_error", "run_cancelled", "run_failed",
                          "run_finished"} or str(event_type).endswith("_error"):
            self._timeline_dirty = True

        line = compact_event(event, reader)
        if event_type == "model_turn_completed" and not self._turn_has_message:
            line = "分析：本轮模型未提供公开说明。"
        if event_type in {"run_finished", "run_cancelled", "run_failed"} or str(event_type).endswith("_error") or (
            event_type in {"remote_lifecycle", "service_lifecycle", "runtime_lifecycle"}
            and ("failed" in str(event.get("state", "")) or event.get("recovery_required"))
        ):
            line = self._event_summary(event)
        if event_type == "run_state_changed" and event.get("cleanup_status") in {"error", "unknown"}:
            line = "清理状态异常：" + safe_plain_text(event["cleanup_status"]) + "；请检查 Evidence 与恢复记录。"
        if event_type == "tool_result" and event.get("name") == "candidate_submit":
            candidate = candidate_value
            if candidate is None:
                line += " — flag candidate unavailable"
            else:
                result = event.get("result") if isinstance(event.get("result"), dict) else {}
                status = safe_plain_text(str(result.get("status", "unverified")))
                line += f" — Flag 已提交：{safe_plain_text(candidate)}"
        if line is not None:
            self.displayed_events.append(line)
        if self._run_active and event_type not in {"run_finished", "run_cancelled"}:
            self._refresh_run_status()
        if event_type in {"run_finished", "run_cancelled", "run_failed"}:
            if self._run_active:
                self._freeze_elapsed()
            self._run_started_at = None

    def _refresh_timeline(self) -> None:
        log = self.query_one("#timeline", HistoryLog)
        # A scrollbar drag or key press may precede the queued HistoryRequested.
        if log.history_requested:
            self._pause_timeline_follow()
        previous_y = log.scroll_y
        log.rebuilding = True
        log.clear()
        log.write(render_timeline(self._timeline_state), scroll_end=False)
        if not self._follow_timeline:
            self._pending_timeline_events += 1
            button = self.query_one("#follow-timeline", Button)
            button.label = "有新动态 · 查看最新"
            button.add_class("visible")

        def restore_position() -> None:
            # Layout must finish before clamping to the new scroll extent.
            if self._follow_timeline:
                log.scroll_end(animate=False, immediate=True)
            else:
                log.scroll_to(y=previous_y, animate=False, immediate=True)
            log.rebuilding = False

        self.call_after_refresh(restore_position)

    def _pause_timeline_follow(self) -> None:
        if not self._follow_timeline:
            return
        self._follow_timeline = False
        button = self.query_one("#follow-timeline", Button)
        button.label = "查看最新"
        button.add_class("visible")

    def _resume_timeline_follow(self) -> None:
        self._follow_timeline = True
        self._pending_timeline_events = 0
        button = self.query_one("#follow-timeline", Button)
        button.remove_class("visible")
        log = self.query_one("#timeline", HistoryLog)
        log.history_requested = False
        log.scroll_end(animate=False, immediate=True)

    def _refresh_run_status(self) -> None:
        if not self._run_active:
            return
        elapsed = int(time.monotonic() - self._run_started_at) if self._run_started_at is not None else 0
        run_id = f" · Run {self._run_id[:8]}" if self._run_id else ""
        candidate = f" · 候选 {self.candidate_status}" if self.candidate_status else ""
        self._set_status(f"运行中{run_id} · 第 {self._turn_count} 轮 · "
                         f"工具调用 {self._tool_count} 次 · 已运行 {elapsed//60:02d}:{elapsed%60:02d}{candidate}")

    def _event_summary(self, event: dict[str, Any]) -> str:
        event_type = str(event.get("event_type", "unknown"))
        sequence = safe_plain_text(str(event.get("seq", "?")))
        if event_type == "run_started":
            detail = f"run {safe_plain_text(str(event.get('run_id', 'unknown')))} started"
        elif event_type == "sandbox_error":
            detail = "沙箱已关闭，本次解题终止；请检查此前的超时或清理错误并重新运行。"
        elif event_type == "run_metadata":
            detail = "run inputs, limits, and provider recorded"
        elif event_type == "remote_lifecycle":
            state = safe_plain_text(str(event.get("state", "unknown")))
            endpoint = safe_plain_text(str(event.get("endpoint", "unknown")))
            detail = f"remote {state}; endpoint {endpoint}"
            if event.get("status"):
                detail += f"; {safe_plain_text(str(event['status']))}"
            if state == "cleanup_failed":
                detail += "; inspect recovery with `ctfbot remote recover`"
        elif event_type == "service_lifecycle":
            state = safe_plain_text(str(event.get("state", "unknown")))
            endpoint = safe_plain_text(str(event.get("endpoint", "unknown")))
            detail = f"service {state}; endpoint {endpoint}"
            if state == "cleanup_failed" and event.get("recovery_journal"):
                detail += "; inspect recovery with `ctfbot service recover`"
        elif event_type == "model_turn_started":
            detail = f"model turn {safe_plain_text(str(event.get('turn', '?')))} started"
        elif event_type == "model_turn_completed":
            detail = f"model turn {safe_plain_text(str(event.get('turn', '?')))} completed"
        elif event_type == "tool_call":
            detail = (
                f"tool call {safe_plain_text(str(event.get('tool_index', '?')))}: "
                f"{safe_plain_text(str(event.get('name', 'unknown')))}"
            )
        elif event_type == "tool_result":
            result = event.get("result") if isinstance(event.get("result"), dict) else {}
            parts = [safe_plain_text(str(event.get("name", "unknown"))),
                     safe_plain_text(str(result.get("status", "unknown")))]
            if result.get("verification_method"):
                parts.append(safe_plain_text(str(result["verification_method"])))
            refs = self._artifact_refs(result)
            if refs:
                parts.extend(safe_plain_text(ref) for ref in refs)
            detail = "tool result: " + " — ".join(parts)
        elif event_type == "run_cancelled":
            detail = f"run stopped ({safe_plain_text(str(event.get('reason', 'user request')))})"
        elif event_type == "run_finished":
            detail = (
                f"run finished: {safe_plain_text(str(event.get('status', 'unknown')))}; "
                f"reason {safe_plain_text(str(event.get('stop_reason', 'unknown')))}"
            )
        elif event_type == "run_failed" or event_type.endswith("_error"):
            error_type = event.get("kind", event.get("error_type", "error"))
            detail = f"{safe_plain_text(str(error_type))}; details remain in private evidence"
        elif event_type == "ui_error":
            detail = "run error: " + safe_plain_text(str(event.get("error_type", "unknown error")))
        else:
            detail = safe_plain_text(event_type.replace("_", " "))
        return f"#{sequence} {detail}"

    @staticmethod
    def _artifact_refs(value: Any) -> list[str]:
        refs: set[str] = set()
        if isinstance(value, dict):
            artifact = value.get("artifact")
            if isinstance(artifact, str) and artifact.startswith("artifacts/"):
                refs.add(artifact)
            for item in value.values():
                refs.update(CTFBotApp._artifact_refs(item))
        elif isinstance(value, list):
            for item in value:
                refs.update(CTFBotApp._artifact_refs(item))
        return sorted(refs)

    @staticmethod
    def _read_candidate_value(event: dict[str, Any], evidence_dir: Path | None) -> str | None:
        """Read a bounded candidate only from its private, hash-verified evidence artifact."""
        result = event.get("result")
        candidate_ref = result.get("candidate_evidence") if isinstance(result, dict) else None
        return CTFBotApp._read_artifact(candidate_ref, evidence_dir, limit=4096)

    @staticmethod
    def _read_artifact(candidate_ref: Any, evidence_dir: Path | None, *, limit: int) -> str | None:
        if evidence_dir is None:
            return None
        if not isinstance(candidate_ref, dict):
            return None
        artifact = candidate_ref.get("artifact")
        digest = candidate_ref.get("sha256")
        expected_bytes = candidate_ref.get("bytes")
        if (
            not isinstance(artifact, str)
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or not isinstance(expected_bytes, int)
            or isinstance(expected_bytes, bool)
            or not 0 <= expected_bytes <= limit
        ):
            return None
        match = re.fullmatch(r"artifacts/(sha256-[0-9a-f]{64}\.bin)", artifact)
        if match is None or match.group(1) != f"sha256-{digest}.bin":
            return None

        try:
            if evidence_dir.is_symlink():
                return None
            run_dir = evidence_dir.resolve(strict=True)
            artifact_dir = run_dir / "artifacts"
            if artifact_dir.is_symlink() or not artifact_dir.is_dir():
                return None
            directory_info = artifact_dir.stat()
            if not stat.S_ISDIR(directory_info.st_mode) or stat.S_IMODE(directory_info.st_mode) & 0o077:
                return None
            artifact_path = artifact_dir / match.group(1)
            if artifact_path.is_symlink():
                return None
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(artifact_path, flags)
            with os.fdopen(fd, "rb") as stream:
                file_info = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(file_info.st_mode)
                    or stat.S_IMODE(file_info.st_mode) & 0o077
                    or file_info.st_size != expected_bytes
                ):
                    return None
                raw = stream.read(limit + 1)
            if len(raw) != expected_bytes or hashlib.sha256(raw).hexdigest() != digest:
                return None
            return raw.decode("utf-8")
        except (OSError, UnicodeError, ValueError):
            return None

    def _record_candidate(self, event: dict[str, Any], value: str, *, notify: bool = True) -> None:
        result = event.get("result", {})
        if not isinstance(result, dict) or result.get("status") != "unverified":
            return
        cid = str(result.get("candidate_id") or f"candidate-{len(self._candidates) + 1}")
        if cid in self._candidates:
            return
        candidate = FlagCandidate(cid, value, str(result.get("status", "unverified")), str(result.get("subtask") or ""))
        self._candidates[cid] = candidate
        self._sync_candidates()
        if notify and self._run_active:
            label = safe_plain_text(candidate.subtask) or "未标注子题"
            self.notify(f"Flag 已提交：{label} · {safe_plain_text(cid)}，对应目标已完成", timeout=3, markup=False)

    def _sync_candidates(self) -> None:
        button = self.query_one("#candidates", Button)
        button.label = f"Flag · {len(self._candidates)}" if self._run_active or self._candidates else "Flag"
        button.disabled = not (self._run_active or self.last_result is not None or self._candidates)
        for screen in self.screen_stack:
            if isinstance(screen, CandidateScreen) and screen.is_mounted:
                screen.update_candidates(tuple(self._candidates.values()), running=self._run_active)

    def _show_candidates(self) -> None:
        if isinstance(self.screen, CandidateScreen):
            return
        if not (self._run_active or self.last_result is not None or self._candidates):
            return
        if not self._run_active and self.last_result is not None:
            # Re-read historical artifacts to preserve integrity checks even on reopen.
            run_dir = Path(self.last_result.run_dir)
            self._candidates.clear()
            try:
                with (run_dir / "events.jsonl").open(encoding="utf-8") as stream:
                    for line in stream:
                        if not line.strip():
                            continue
                        event = json.loads(line)
                        if (not isinstance(event, dict) or event.get("event_type") != "tool_result"
                                or event.get("name") != "candidate_submit"
                                or event.get("run_id", self.last_result.run_id) != self.last_result.run_id):
                            continue
                        value = self._read_candidate_value(event, run_dir)
                        if value is not None:
                            self._record_candidate(event, value, notify=False)
            except (OSError, ValueError) as exc:
                self._set_status(f"候选读取失败：{safe_plain_text(str(exc))}")
            self._sync_candidates()
        self.push_screen(CandidateScreen(tuple(self._candidates.values()), running=self._run_active))
        # An event may arrive between push_screen and the modal mounting.
        self.call_after_refresh(self._sync_candidates)

    def _select_run_view(self, view: str) -> None:
        self.query_one("#run-panels").display = view == "activity"
        self.query_one("#run-document").display = view != "activity"
        for button_id in ("evidence", "report", "writeup"):
            self.query_one(f"#{button_id}", Button).variant = "primary" if view == button_id else "default"
        if view != "activity":
            self.default_screen.add_class("run-view")
            self.query_one("#toggle-layout", Button).label = "Log"

    def _show_evidence(self) -> None:
        if self.last_result is None:
            self._set_status("Evidence is available after the run finishes.")
            return
        path = Path(self.last_result.run_dir) / "events.jsonl"
        try:
            self.query_one("#run-document", RunDocumentView).show_evidence(path)
        except (OSError, UnicodeError) as exc:
            self._set_status(f"Evidence read error: {safe_plain_text(str(exc))}")
            return
        self._select_run_view("evidence")
        self._set_status("正在查看 events.jsonl：JSON 格式化与高亮；上一页 / 下一页翻阅，Log 返回运行记录。")

    def _write_report(self) -> None:
        if self.last_result is None:
            self._set_status("A report is available after the run finishes.")
            return
        try:
            self.last_report_path = generate_basic_report(Path(self.last_result.run_dir))
            self.query_one("#run-document", RunDocumentView).show_report(self.last_report_path)
        except (OSError, ValueError) as exc:
            self._set_status(f"Report error: {safe_plain_text(str(exc))}")
            return
        self._select_run_view("report")
        self._set_status(f"正在查看 Markdown 报告：{self.last_report_path}；Log 返回运行记录。")

    def _show_writeup(self) -> None:
        if self.last_result is None:
            self._set_status("任务完成后可查看 Writeup。")
            return
        path = Path(self.last_result.run_dir) / "writeup.md"
        try:
            self.query_one("#run-document", RunDocumentView).show_report(path, title="WRITEUP / 解题总结")
        except (OSError, UnicodeError) as exc:
            self._set_status(f"Writeup 读取失败：{safe_plain_text(exc)}")
            return
        self._select_run_view("writeup")
        self._set_status(f"正在查看 agent 解题总结：{path}；Log 返回运行记录。")

    def _input_paths(self) -> tuple[Path, str, Path]:
        workspace = self.query_one("#workspace-path", Input).value.strip()
        image = self.query_one("#runtime-image", Input).value.strip()
        runs_root = self.query_one("#runs-root", Input).value.strip()
        if not all((workspace, runs_root)):
            raise ValueError("workspace and run output path are required")
        if not image:
            if self._runtime_lookup_active:
                raise ValueError("Local runtime lookup is still running; preview again shortly.")
            choice = self._runtime_resolver()
            if not choice.image:
                raise ValueError(choice.message)
            image = choice.image
            self.query_one("#runtime-image", Input).value = image
        return Path(workspace), image, Path(runs_root)

    def _set_preview(self, value: str) -> None:
        self._preview_text = "\n".join(safe_plain_text(line) for line in value.split("\n"))
        self.query_one("#preview-details", Static).update(self._as_text(self._preview_text))

    def _set_status(self, value: str) -> None:
        self.status_text = safe_plain_text(value)
        self.query_one("#status", Static).update(self._as_text(self.status_text))
        elapsed = int(time.monotonic() - self._run_started_at) if self._run_started_at is not None else 0
        if self._run_active:
            phase = "RUNNING"
        elif self.last_result:
            phase = "FINISHED"
        elif self._preview and self._preview.start_allowed:
            phase = "READY"
        else:
            phase = "IDLE"
        if not self._run_active:
            elapsed = self._run_elapsed_seconds
        metrics = Text(f"{phase}  ", style="bold cyan" if self._run_active else "bold")
        metrics.append(f"·  轮次 {self._turn_count}  ·  工具 {self._tool_count}  ·  {elapsed//60:02d}:{elapsed%60:02d}", style="dim")
        self.query_one("#metrics", Static).update(metrics)

    @staticmethod
    def _as_text(value: str) -> Text:
        return Text(value, end="")


def run_tui(*, service_profile: Path | None = None, remote_profile: Path | None = None,
            remote_grant: Path | None = None, memory_root: Path | None = None,
            memory_namespaces: tuple[str, ...] = (), evaluation_mode: str = "blind") -> int:
    if not sys.stdin.isatty():
        print(
            "ctfbot needs an interactive terminal; use `ctfbot doctor` for diagnostics.",
            file=sys.stderr,
        )
        return 2
    CTFBotApp(service_factory=lambda: create_codex_application_service(service_profile=service_profile,
                                                                     remote_profile=remote_profile,
                                                                     remote_grant=remote_grant, memory_root=memory_root,
                                                                     memory_namespaces=memory_namespaces,
                                                                     evaluation_mode=evaluation_mode)).run()
    return 0
