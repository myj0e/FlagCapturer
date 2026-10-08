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
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.events import MouseScrollDown, MouseScrollUp, Resize
from textual.widgets import Button, Checkbox, Footer, Header, Input, Label, RichLog, Static

from ctfbot.agent.loop import RunLimits, RunResult
from ctfbot.application.control import RunControl
from ctfbot.application.runtime_images import RuntimeImageChoice, resolve_runtime_image
from ctfbot.application.service import ChallengePreview, LocalChallengeService, create_codex_application_service
from ctfbot.challenge.single_file import import_single_file, select_single_attachment
from ctfbot.reporting import generate_basic_report
from ctfbot.tui.candidates import CandidateScreen, FlagCandidate
from ctfbot.tui.safe_text import SafePathInput, safe_plain_text
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

    TITLE = "ctfbot — local challenge run"
    BINDINGS = [("ctrl+q", "request_quit", "Quit"), ("f2", "toggle_layout", "Config / Log"), ("f3", "toggle_summary", "摘要")]
    CSS = """
    Screen { padding: 0; }
    #body { height: 1fr; }
    #fields { height: 12; padding: 0 1; }
    .form-row { height: 2; }
    .form-label { width: 22; content-align: left middle; }
    Input { width: 1fr; height: 2; border: none; }
    Checkbox { height: 2; padding: 0; border: none; }
    #actions { height: 3; padding: 0 1; }
    Button { margin-right: 1; min-width: 10; }
    #status { height: 3; padding: 0 1; border: round $accent; }
    #preview-details { height: 5; padding: 0 1; border: round $primary; overflow-y: auto; }
    #run-panels { height: 1fr; }
    #timeline-panel { width: 3fr; height: 1fr; }
    #summary-panel { display: none; width: 1fr; height: 1fr; border: round $accent; padding: 0 1; }
    #solve-summary { height: auto; }
    Screen.run-view #summary-panel { display: block; }
    Screen.summary-hidden #summary-panel { display: none; }
    #run-panels.narrow { layout: vertical; }
    #run-panels.narrow #timeline-panel { width: 1fr; height: 3fr; }
    #run-panels.narrow #summary-panel { width: 1fr; height: 1fr; min-height: 6; }
    #timeline { height: 1fr; min-height: 4; border: round $secondary; padding: 0 1; }
    #follow-timeline { display: none; min-width: 25; }
    #follow-timeline.visible { display: block; }
    Screen.run-view #fields { display: none; }
    Screen.run-view #preview-details { display: none; }
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
        self._event_queue: queue.SimpleQueue[tuple[str, Any]] = queue.SimpleQueue()
        self._worker_thread: threading.Thread | None = None
        self._run_active = False
        self._quit_after_run = False
        self.last_result: RunResult | None = None
        self.last_report_path: Path | None = None
        self.candidate_status: str | None = None
        self.displayed_events: list[str] = []
        self._timeline_state = TimelineState()
        self._timeline_dirty = False
        self._follow_timeline = True
        self._pending_timeline_events = 0
        self._run_started_at: float | None = None
        self._run_id = ""
        self._turn_count = 0
        self._tool_count = 0
        self._turn_has_message = False
        self._solving_summary = SolvingSummary()
        self._preview_text = ""
        self.status_text = "Select a challenge file or imported workspace, then Preview before running."

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="body"):
            with Vertical(id="fields"):
                with Horizontal(classes="form-row"):
                    yield Label("Challenge file / folder", classes="form-label")
                    yield SafePathInput(placeholder="Single executable/attachment, or imported workspace directory", id="workspace-path")
                with Horizontal(classes="form-row"):
                    yield Label("File model transfer", classes="form-label")
                    yield FileTransferCheckbox("未允许 / OFF：附件与工具输出不可发送给模型（点击允许）", id="file-model-transfer")
                with Horizontal(classes="form-row"):
                    yield Label("Oracle (optional)", classes="form-label")
                    yield SafePathInput(placeholder="Leave blank when the flag is unknown", id="oracle-path")
                with Horizontal(classes="form-row"):
                    yield Label("补充提示词（可选）", classes="form-label")
                    yield SafePathInput(value=self.additional_prompt, placeholder="可填写解题线索、已知 flag 格式或其他提示", id="additional-prompt")
                with Horizontal(classes="form-row"):
                    yield Label("Runtime (automatic)", classes="form-label")
                    yield SafePathInput(value=self.runtime_image,
                                        placeholder="Selected automatically; optional pinned image override",
                                        id="runtime-image")
                with Horizontal(classes="form-row"):
                    yield Label("Private run output", classes="form-label")
                    yield SafePathInput(value=str(self.runs_root), id="runs-root")
            with Horizontal(id="actions"):
                yield Button("Preview", id="preview", variant="primary")
                yield Button("Run", id="run", disabled=True, variant="success")
                yield Button("Stop", id="stop", disabled=True, variant="error")
                yield Button("Evidence", id="evidence", disabled=True)
                yield Button("Report", id="report", disabled=True)
                yield Button("Flag", id="candidates", disabled=True, tooltip="运行结束后选择候选 flag 并复制")
                yield Button("Quit", id="quit")
                yield Button("Log", id="toggle-layout", tooltip="F2：切换配置与大记录视图")
            yield Static(self._as_text(self.status_text), id="status")
            yield Static(Text("填写路径 → 按需勾选模型传输许可 → Preview 检查题目 → Run 开始。预览详情可滚动查看。"), id="preview-details")
            with Horizontal(id="run-panels"):
                with Vertical(id="timeline-panel"):
                    yield Button("新记录 · 返回最新", id="follow-timeline", tooltip="恢复自动跟随最新记录")
                    yield RichLog(id="timeline", wrap=True, markup=False, max_lines=2000)
                with VerticalScroll(id="summary-panel"):
                    yield Static(self._solving_summary.render(), id="solve-summary")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#run-panels").set_class(self.size.width < 110, "narrow")
        self.query_one("#status", Static).border_title = "1. 状态与操作提示"
        self.query_one("#preview-details", Static).border_title = "2. 题目预览 · 附件 / 授权 / 验证方式（可滚动）"
        self.query_one("#timeline", RichLog).border_title = "3. 运行记录 · 每轮分析 → 工具 → 返回 · F2 展开配置"
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
        elif action == "quit":
            self.action_request_quit()
        elif action == "toggle-layout":
            self.action_toggle_layout()
        elif action == "follow-timeline":
            self._resume_timeline_follow()

    def on_resize(self, event: Resize) -> None:
        if self.is_mounted:
            self.query_one("#run-panels").set_class(event.size.width < 110, "narrow")

    def action_toggle_summary(self) -> None:
        self.screen.toggle_class("summary-hidden")

    def _refresh_summary(self) -> None:
        self.query_one("#solve-summary", Static).update(self._solving_summary.render())

    def _reset_summary(self) -> None:
        self._solving_summary = SolvingSummary()
        self._refresh_summary()

    def on_mouse_scroll_up(self, event: MouseScrollUp) -> None:
        if getattr(event.widget, "id", None) == "timeline":
            self._pause_timeline_follow()

    def on_mouse_scroll_down(self, event: MouseScrollDown) -> None:
        widget = event.widget
        if getattr(widget, "id", None) == "timeline" and widget.scroll_y >= widget.max_scroll_y - 1:
            self._resume_timeline_follow()

    def action_toggle_layout(self) -> None:
        self.screen.toggle_class("run-view")
        self.query_one("#toggle-layout", Button).label = "Config" if self.screen.has_class("run-view") else "Log"

    def _expand_log(self) -> None:
        self.screen.add_class("run-view")
        self.query_one("#toggle-layout", Button).label = "Config"
        self.query_one("#timeline", RichLog).focus()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id != "file-model-transfer":
            return
        event.checkbox.label = ("已允许 / ON：附件与工具输出可发送给配置的模型（点击撤销）"
                                if event.value else "未允许 / OFF：附件与工具输出不可发送给模型（点击允许）")
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
        self.screen.remove_class("run-view")
        self.query_one("#toggle-layout", Button).label = "Log"
        self.query_one("#run", Button).disabled = True
        self.query_one("#evidence", Button).disabled = True
        self.query_one("#report", Button).disabled = True
        try:
            workspace, oracle, image, runs_root = self._input_paths()
            attachment = select_single_attachment(workspace)
            if attachment is not None:
                if self.imports_root.resolve().is_relative_to(runs_root.resolve()) or runs_root.resolve().is_relative_to(self.imports_root.resolve()):
                    raise ValueError("challenge imports and run output directories must be disjoint")
                workspace = import_single_file(attachment, self.imports_root,
                    authorize_model_data=self.query_one("#file-model-transfer", Checkbox).value)
            preview = self.service.preview(workspace, oracle, image, runs_root, self.limits,
                                           additional_prompt=self.query_one("#additional-prompt", Input).value)
        except Exception as exc:
            self._preview = None
            self._set_preview("Preview failed / 预览失败：" + safe_plain_text(str(exc)) +
                              "\n可选择单个附件、仅含一个附件的普通目录，或已导入的 workspace。")
            self._set_status(f"Preview error: {safe_plain_text(str(exc))}")
            return
        self._preview = preview
        self.candidate_status = None
        self.last_result = None
        self.query_one("#candidates", Button).disabled = True
        self.last_report_path = None
        self.displayed_events.clear()
        self._turn_count = 0
        self._tool_count = 0
        self._run_id = ""
        self._run_started_at = None
        self._reset_summary()
        self._timeline_state.clear()
        self._timeline_dirty = False
        self._follow_timeline = True
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
            "Limits: " + ", ".join(
                f"{key}={safe_plain_text('unlimited (count only)' if key == 'max_tool_calls' and value is None else value)}"
                for key, value in asdict(preview.limits).items()
            ),
            "Inputs:",
        ]
        if preview.service_endpoint:
            lines.insert(-1, f"Authorized endpoint: {safe_plain_text(preview.service_endpoint)}")
        if preview.additional_prompt.strip():
            lines.insert(-1, f"补充提示词: {safe_plain_text(preview.additional_prompt)}")
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
            workspace, oracle, image, runs_root = self._input_paths()
        except ValueError as exc:
            self._set_status(f"Run blocked: {safe_plain_text(str(exc))}")
            return
        workspace = Path(self._preview.workspace)

        self._run_active = True
        self._reset_summary()
        self._expand_log()
        self._control = RunControl()
        self.candidate_status = None
        self.last_result = None
        self.query_one("#candidates", Button).disabled = True
        self.last_report_path = None
        self._turn_count = 0
        self._tool_count = 0
        self.query_one("#preview", Button).disabled = True
        self.query_one("#run", Button).disabled = True
        self.query_one("#stop", Button).disabled = False
        self.query_one("#evidence", Button).disabled = True
        self.query_one("#report", Button).disabled = True
        self._set_status("Run starting. Model and runtime will be created after preflight succeeds.")
        control = self._control
        additional_prompt = self._preview.additional_prompt

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
                self._event_queue.put(("event", (record, evidence_dir)))

            try:
                result = self.service.run(
                    workspace,
                    oracle,
                    image,
                    runs_root,
                    self.limits,
                    control=control,
                    event_sink=publish_event,
                    additional_prompt=additional_prompt,
                )
            except Exception as exc:
                self._event_queue.put(("error", exc))
            else:
                self._event_queue.put(("result", result))

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

    def _finish_run(self, result: RunResult) -> None:
        self.last_result = result
        self._run_active = False
        self._control = None
        self.query_one("#preview", Button).disabled = False
        self.query_one("#stop", Button).disabled = True
        self.query_one("#evidence", Button).disabled = False
        self.query_one("#report", Button).disabled = False
        self.query_one("#candidates", Button).disabled = False
        self.query_one("#run", Button).disabled = True
        if result.status == "verified":
            message = f"Run verified by controller exact-string verifier. Evidence: {result.run_dir}"
        elif result.status == "format_only":
            message = f"Candidate matches the flag format; correctness is unverified. Evidence: {result.run_dir}"
        elif result.status == "candidate_unverified":
            message = f"已结束，保留未验证候选；correctness is unverified。Evidence: {result.run_dir}"
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
        self._run_active = False
        self._control = None
        self.query_one("#preview", Button).disabled = False
        self.query_one("#stop", Button).disabled = True
        self.query_one("#run", Button).disabled = self._preview is None or not self._preview.start_allowed
        self.query_one("#evidence", Button).disabled = self.last_result is None
        self.query_one("#report", Button).disabled = self.last_result is None
        self._solving_summary.outcome = "运行错误"
        self._refresh_summary()
        error_type = safe_plain_text(type(error).__name__)
        self._render_event({"event_type": "ui_error", "error_type": error_type})
        self._set_status(f"Run error ({error_type}). Check the workspace, model setup, and runtime.")
        if self._quit_after_run:
            self.exit()

    def _render_event(self, event: dict[str, Any], *, evidence_dir: Path | None = None) -> None:
        event_type = event.get("event_type", "unknown")
        if event_type == "run_started":
            self._run_id = str(event.get("run_id", ""))
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
            if isinstance(result, dict):
                self.candidate_status = str(result.get("status", "candidate"))

        reader = lambda ref: self._read_artifact(ref, evidence_dir, limit=256 * 1024)
        candidate_value = (self._read_candidate_value(event, evidence_dir)
                           if event_type == "tool_result" and event.get("name") == "candidate_submit" else None)
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
                line += f" — flag candidate ({status}): {safe_plain_text(candidate)}"
        if line is not None:
            self.displayed_events.append(line)
        if self._run_active and event_type not in {"run_finished", "run_cancelled"}:
            self._refresh_run_status()
        if event_type in {"run_finished", "run_cancelled", "run_failed"}:
            self._run_started_at = None

    def _refresh_timeline(self) -> None:
        log = self.query_one("#timeline", RichLog)
        previous_y = log.scroll_y
        log.clear()
        log.write(render_timeline(self._timeline_state), scroll_end=self._follow_timeline)
        if self._follow_timeline:
            log.scroll_end(animate=False)
        else:
            self._pending_timeline_events += 1
            log.scroll_to(y=min(previous_y, log.max_scroll_y), animate=False, immediate=True)
            button = self.query_one("#follow-timeline", Button)
            button.label = "有新动态 · 返回最新"
            button.add_class("visible")

    def _pause_timeline_follow(self) -> None:
        self._follow_timeline = False

    def _resume_timeline_follow(self) -> None:
        self._follow_timeline = True
        self._pending_timeline_events = 0
        button = self.query_one("#follow-timeline", Button)
        button.remove_class("visible")
        log = self.query_one("#timeline", RichLog)
        log.scroll_end(animate=False)

    def _refresh_run_status(self) -> None:
        if not self._run_active:
            return
        elapsed = int(time.monotonic() - self._run_started_at) if self._run_started_at is not None else 0
        run_id = f" · Run {self._run_id[:8]}" if self._run_id else ""
        candidate = f" · 候选 {self.candidate_status}" if self.candidate_status else ""
        self._set_status(f"运行中{run_id} · 第 {self._turn_count}/{self.limits.max_turns} 轮 · "
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

    def _show_candidates(self) -> None:
        if self._run_active or self.last_result is None:
            return
        run_dir = Path(self.last_result.run_dir)
        candidates: list[FlagCandidate] = []
        try:
            with (run_dir / "events.jsonl").open(encoding="utf-8") as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    event = json.loads(line)
                    if not isinstance(event, dict) or event.get("event_type") != "tool_result" or event.get("name") != "candidate_submit":
                        continue
                    value = self._read_candidate_value(event, run_dir)
                    if value is None:
                        continue
                    result = event["result"]
                    candidates.append(FlagCandidate(
                        str(result.get("candidate_id", f"candidate-{len(candidates) + 1}")),
                        value, str(result.get("status", "unverified")),
                    ))
        except (OSError, ValueError) as exc:
            self._set_status(f"候选读取失败：{safe_plain_text(str(exc))}")
            return
        self.push_screen(CandidateScreen(tuple(candidates)))

    def _show_evidence(self) -> None:
        if self.last_result is None:
            self._set_status("Evidence is available after the run finishes.")
            return
        try:
            events_path = Path(self.last_result.run_dir) / "events.jsonl"
            events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line]
        except (OSError, json.JSONDecodeError) as exc:
            self._set_status(f"Evidence read error: {safe_plain_text(str(exc))}")
            return
        self.displayed_events.clear()
        self._turn_count = self._tool_count = 0
        self._turn_has_message = False
        self._reset_summary()
        self._timeline_state.clear()
        self._timeline_dirty = False
        self._follow_timeline = True
        self._pending_timeline_events = 0
        self.query_one("#follow-timeline", Button).remove_class("visible")
        self._expand_log()
        log = self.query_one("#timeline", RichLog)
        log.clear()
        for event in events:
            if isinstance(event, dict):
                self._render_event(event, evidence_dir=Path(self.last_result.run_dir))
        if self._timeline_dirty:
            self._timeline_dirty = False
            self._refresh_timeline()
        self._set_status(f"精简回看：分析 / 工具 / 返回。完整 events.jsonl 与 artifacts 保存在 {self.last_result.run_dir}")

    def _write_report(self) -> None:
        if self.last_result is None:
            self._set_status("A report is available after the run finishes.")
            return
        try:
            self.last_report_path = generate_basic_report(Path(self.last_result.run_dir))
        except (OSError, ValueError) as exc:
            self._set_status(f"Report error: {safe_plain_text(str(exc))}")
            return
        self._set_status(f"Basic report written to {safe_plain_text(str(self.last_report_path))}")

    def _input_paths(self) -> tuple[Path, Path | None, str, Path]:
        workspace = self.query_one("#workspace-path", Input).value.strip()
        oracle = self.query_one("#oracle-path", Input).value.strip()
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
        return Path(workspace), Path(oracle) if oracle else None, image, Path(runs_root)

    def _set_preview(self, value: str) -> None:
        self._preview_text = "\n".join(safe_plain_text(line) for line in value.split("\n"))
        self.query_one("#preview-details", Static).update(self._as_text(self._preview_text))

    def _set_status(self, value: str) -> None:
        self.status_text = safe_plain_text(value)
        self.query_one("#status", Static).update(self._as_text(self.status_text))

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
