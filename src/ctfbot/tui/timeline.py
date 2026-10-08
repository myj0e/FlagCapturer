"""Concise public model/tool activity; full evidence remains on disk."""
from __future__ import annotations

import json
import shlex
from datetime import datetime
from collections.abc import Callable
from typing import Any

from rich.text import Text

from ctfbot.tui.safe_text import safe_plain_text


IGNORED_EVENTS = {"run_metadata", "command_execution", "workflow_read", "domain_routing",
                 "run_state_changed", "observation_recorded", "candidate_recorded",
                 "candidate_check_recorded", "experiment_recorded", "claim_recorded",
                 "model_turn_started", "summary_updated", "context_index_updated", "context_snapshot"}


class TimelineState:
    """Group the existing event stream into turns and call/result cards."""
    def __init__(self) -> None:
        self.rounds: list[dict[str, Any]] = []
        self.notices: list[dict[str, Any]] = []

    def clear(self) -> None:
        self.rounds.clear()
        self.notices.clear()

    def add(self, event: dict[str, Any], read: Callable[[Any], str | None],
            candidate: str | None = None) -> None:
        kind = event.get("event_type")
        turn = event.get("turn")
        if kind == "model_turn_started":
            self.rounds.append({"turn": turn or len(self.rounds)+1, "state": "进行中",
                                "started": event.get("timestamp_utc"), "ended": None, "entries": []})
            return
        if kind in {"run_started", "run_finished", "run_metadata"} or kind in IGNORED_EVENTS:
            if kind == "run_finished":
                self.notices.append(event)
            return
        current = next((r for r in reversed(self.rounds) if turn is None or r["turn"] == turn), None)
        if current is None and kind not in {"tool_result", "tool_call"}:
            self.notices.append(event)
            return
        if current is None:
            current = {"turn": turn or 1, "state": "进行中", "started": None, "ended": None, "entries": []}
            self.rounds.append(current)
        if kind == "model_turn_completed":
            current["state"] = "已完成"
            current["ended"] = event.get("timestamp_utc")
            if not current["entries"] or current["entries"][-1].get("kind") != "model":
                current["entries"].append({"kind": "model", "text": "本轮没有公开说明。"})
            return
        if kind == "assistant_message":
            text = read(event.get("evidence")) or "模型未提供可读取的公开说明。"
            current["entries"].append({"kind": "model", "text": text})
            return
        if kind == "tool_call":
            raw = read(event.get("arguments_artifact"))
            try:
                args = json.loads(raw) if raw else {}
            except (TypeError, ValueError):
                args = {}
            current["entries"].append({"kind": "tool", "index": event.get("tool_index"),
                                       "name": event.get("name", "unknown"), "args": args,
                                       "result": None, "candidate": None})
            return
        if kind == "tool_result":
            index = event.get("tool_index")
            card = next((e for e in reversed(current["entries"])
                         if e.get("kind") == "tool" and e.get("index") == index), None)
            if card is None:
                card = {"kind": "tool", "index": index, "name": event.get("name", "unknown"),
                        "args": {}, "result": None, "candidate": None}
                current["entries"].append(card)
            card["result"] = event
            card["candidate"] = candidate
            reply = read(event.get("response_evidence"))
            card["reply_text"] = _reply_summary(reply, card.get("name", ""))
            if _is_problem_result(event.get("result", {})):
                card["emphasis"] = "error"
            return
        if kind in {"completion_recorded", "sandbox_error", "run_cancelled", "run_failed"} or str(kind).endswith("_error"):
            current["entries"].append({"kind": "notice", "event": event,
                                       "emphasis": "error" if kind != "completion_recorded" else "completion"})
            return
        if kind not in IGNORED_EVENTS:
            current["entries"].append({"kind": "notice", "event": event})


def _is_problem_result(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    status = str(value.get("status", "")).lower()
    return any(term in status for term in ("error", "failed", "timeout", "denied", "rejected")) or bool(value.get("error_kind"))


def _reply_summary(raw: str | None, name: str) -> str:
    if not raw:
        return ""
    try:
        value = json.loads(raw)
    except ValueError:
        return raw
    if not isinstance(value, dict):
        return str(value)
    if name == "workflow_discover":
        triage = value.get("triage", {})
        labels = triage.get("labels", []) if isinstance(triage, dict) else []
        packs = value.get("packs", {})
        names = list(packs) if isinstance(packs, dict) else packs if isinstance(packs, list) else []
        return f"可用工作流：{', '.join(map(str, names))}；题型线索：{', '.join(map(str, labels))}"
    fields = []
    if value.get("output_warning") == "ordinary_pool_80_percent":
        fields.append("工具回复额度已使用 80%，后续返回缩短；状态与证据仍可读取。")
    for stream, preview in value.get("previews", {}).items():
        if isinstance(preview, dict):
            fields.append(f"{stream} 头部：{preview.get('head', '')}\n{stream} 尾部：{preview.get('tail', '')}")
    for key in ("stdout", "output", "output_preview", "preview", "stderr", "message", "content"):
        item = value.get(key)
        if isinstance(item, str) and item:
            label = "错误输出" if key == "stderr" else "输出"
            fields.append(f"{label}：{item}")
    if not fields:
        ignored = {"status", "observation", "observation_id", "raw_response_evidence", "raw_response_artifact",
                   "evidence", "source", "coverage", "run_budget", "source_coordinates", "output_evidence",
                   "transcript", "artifact", "sha256", "stdout_evidence", "stderr_evidence", "execution"}
        small = {key: item for key, item in value.items()
                 if key not in ignored and not key.endswith(("_evidence", "_sha256"))
                 and isinstance(item, (str, int, float, bool, type(None)))}
        fields = [json.dumps(small, ensure_ascii=False, sort_keys=True)] if small else []
    return "\n".join(fields)


def render_timeline(state: TimelineState) -> Text:
    """Render labeled blocks; meaning remains clear without color support."""
    output = Text()
    output.append("上下文占用：未知（provider 未提供当前占用计数）\n", style="dim")
    for notice in state.notices:
        if notice.get("event_type") == "run_finished":
            continue
        output.append(_notice_text(notice) + "\n", style="bold yellow")
    for turn in state.rounds:
        tools = sum(entry.get("kind") == "tool" for entry in turn["entries"])
        duration = _duration(turn.get("started"), turn.get("ended"))
        suffix = f" · {duration}" if duration else ""
        output.append(f"━━ 第 {turn['turn']} 轮 · {turn['state']} · {tools} 次工具调用{suffix} ━━\n",
                      style="bold cyan")
        for entry in turn["entries"]:
            kind = entry.get("kind")
            if kind == "model":
                output.append("  ◉ 模型说明（公开）\n", style="bold blue")
                output.append("  │ " + clip(entry.get("text", ""), 1600, 12).replace("\n", "\n  │ ") + "\n\n",
                              style="blue")
            elif kind == "tool":
                _append_tool_card(output, entry)
            elif kind == "notice":
                event = entry.get("event", {})
                output.append("  ◆ " + _notice_text(event) + "\n\n",
                              style="bold red" if entry.get("emphasis") == "error" else "bold yellow")
    for notice in state.notices:
        if notice.get("event_type") == "run_finished":
            output.append("\n◆ " + _notice_text(notice) + "\n", style="bold yellow")
    if not state.rounds and not state.notices:
        output.append("等待模型开始本轮……", style="dim")
    return output


def _append_tool_card(output: Text, entry: dict[str, Any]) -> None:
    event = entry.get("result")
    result = event.get("result", {}) if isinstance(event, dict) else {}
    status = safe_plain_text(str(result.get("status", "等待返回")))
    duration = f" · {event['elapsed_seconds']} 秒" if isinstance(event, dict) and event.get("elapsed_seconds") is not None else ""
    error = entry.get("emphasis") == "error"
    style = "bold red" if error else "bold green" if status in {"ok", "local_check_passed", "session_exited", "session_closed"} else "bold cyan"
    title = f"  ┌─ 工具 #{entry.get('index', '?')} · {safe_plain_text(str(entry.get('name', 'unknown')))} · {status}{duration} ─\n"
    output.append(title, style=style)
    args = entry.get("args") if isinstance(entry.get("args"), dict) else {}
    args = {key: value for key, value in args.items() if key not in {"candidate", "source"}}
    if entry.get("name") == "summary_update":
        args = {"goal": args.get("goal", "")}
    if args:
        rendered_args = json.dumps(args, ensure_ascii=False, sort_keys=True)
        output.append("  │ 参数：" + clip(rendered_args, 280, 2).replace("\n", "\n  │ ") + "\n", style="dim")
    if isinstance(event, dict):
        reply_text = entry.get("reply_text")
        if reply_text:
            output.append("  │ 返回：" + clip(reply_text, 1100, 7).replace("\n", "\n  │ ") + "\n", style="red" if error else "white")
        elif result.get("error_kind"):
            output.append("  │ 错误：" + safe_plain_text(str(result["error_kind"])) + "\n", style="bold red")
    if entry.get("candidate") is not None:
        candidate_status = safe_plain_text(str(result.get("status", "unverified")))
        output.append(f"  ├─ 候选 · {candidate_status} · 正确性未确认\n", style="bold yellow")
        output.append("  │ " + safe_plain_text(entry["candidate"]) + "\n", style="yellow")
    output.append("  └────────────────────────────────────────────\n\n", style="dim")


def _notice_text(event: dict[str, Any]) -> str:
    kind = event.get("event_type", "event")
    if kind == "provider_context_usage":
        return f"上下文占用未知 · provider 窗口容量：{event.get('capacity') or '未知'} tokens；账单用量单独记录"
    if kind == "provider_compaction_started":
        return "已观测上下文压缩开始"
    if kind == "provider_compaction_completed":
        return "已观测上下文压缩完成 · 运行状态待补发"
    if kind == "context_restore_delivered":
        return f"运行状态已补发 · revision {event.get('revision')} · {event.get('channel')}"
    if kind == "run_finished":
        return f"运行结束：{event.get('status', 'unknown')} · {event.get('stop_reason', 'unknown')}"
    if kind == "completion_recorded":
        label = "本次尝试未解出" if event.get("outcome") == "unsolved" else "保留未验证候选"
        return f"明确结束：{label} · 待解决项 {event.get('unresolved_count', 0)} 个"
    if kind == "sandbox_error":
        return "沙箱异常：本次运行终止，请检查先前工具错误和恢复记录"
    if kind == "run_cancelled":
        return f"运行已停止：{event.get('reason', '用户请求')}"
    if kind == "run_failed" or str(kind).endswith("_error"):
        return f"运行错误：{event.get('kind', event.get('error_type', kind))}；详情见 Evidence"
    return safe_plain_text(str(event.get("message", kind)))


def _duration(start: Any, end: Any) -> str | None:
    try:
        first = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        last = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        seconds = max(0, int((last-first).total_seconds()))
        return f"{seconds//60:02d}:{seconds%60:02d}"
    except (TypeError, ValueError):
        return None


def without_evidence(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: without_evidence(item) for key, item in value.items()
                if not key.endswith(("_evidence", "_sha256")) and key not in {"evidence", "artifact", "sha256", "observation", "raw_response_artifact", "run_budget", "source_coordinates"}}
    if isinstance(value, list):
        return [without_evidence(item) for item in value]
    return value


def clip(value: object, limit: int = 900, lines: int = 6) -> str:
    text = str(value)
    selected = text.splitlines()[:lines]
    rendered = "\n".join(safe_plain_text(line) for line in selected)
    if len(rendered) > limit or len(text.splitlines()) > lines:
        return rendered[:limit] + " …（已省略，完整内容见 Evidence 文件）"
    return rendered


def compact_event(event: dict[str, Any], read: Callable[[Any], str | None]) -> str | None:
    kind = event.get("event_type")
    name = safe_plain_text(event.get("name", "unknown"))
    if kind == "model_turn_started":
        return f"━━ 第 {event.get('turn', '?')} 轮 ━━\n分析：模型处理中，公开说明会显示在本轮。"
    if kind == "assistant_message":
        text = read(event.get("evidence"))
        return "分析 / 模型说明：\n" + (clip(text) if text else "内容不可用，详见 Evidence 文件。")
    if kind == "completion_recorded":
        label = "本次尝试未解出" if event.get('outcome') == 'unsolved' else "保留未验证候选"
        return f"完成：{label}；待解决项 {event.get('unresolved_count', 0)} 个。"
    if kind == "tool_call":
        raw = read(event.get("arguments_artifact"))
        try:
            args = json.loads(raw) if raw else {}
        except (ValueError, TypeError):
            args = {}
        if not isinstance(args, dict):
            args = {}
        if name not in {"script_run", "script_start"} and args.get("argv") and isinstance(args["argv"], list) and all(isinstance(item, str) for item in args["argv"]):
            detail = shlex.join(args["argv"])
        else:
            selected = {key: value for key, value in args.items() if key not in {"candidate", "source"}}
            detail = json.dumps(selected, ensure_ascii=False) if selected else ""
            if name == "script_save":
                detail += f" · 保存脚本 {len(str(args.get('source', '')))} 字符"
        return f"工具 {event.get('tool_index', '?')} · {name}" + ("\n  " + clip(detail, 260, 2) if detail else "")
    if kind == "tool_result":
        result = event.get("result") if isinstance(event.get("result"), dict) else {}
        status = safe_plain_text(result.get("status", "unknown"))
        header = f"返回 · {name} · {status}"
        if "exit_code" in result:
            header += f" · exit={safe_plain_text(result['exit_code'])}"
        if event.get("elapsed_seconds") is not None:
            header += f" · {safe_plain_text(event['elapsed_seconds'])}s"
        if name == "session_read" and "output_idle_seconds" in result:
            header += f" · 运行 {safe_plain_text(result.get('elapsed_seconds', '?'))}s · 无输出 {safe_plain_text(result['output_idle_seconds'])}s"
        if name == "candidate_submit":
            return header + (f" · {safe_plain_text(result['candidate_id'])}" if result.get('candidate_id') else '')
        raw = read(event.get("response_evidence"))
        if raw is None:
            return header + "\n  返回内容未载入；完整内容见 Evidence 文件。"
        try:
            response = json.loads(raw)
        except ValueError:
            response = raw
        if isinstance(response, dict):
            if name == "workflow_discover":
                packs = response.get("packs")
                triage = response.get("triage")
                response = {"工作流": list(packs) if isinstance(packs, (dict, list)) else [],
                            "题型线索": triage.get("labels", []) if isinstance(triage, dict) else []}
            elif name == "workflow_read":
                metadata = response.get("metadata")
                response = {"已加载工作流": response.get("pack"), "版本": metadata.get("version") if isinstance(metadata, dict) else None}
            elif name == "workflow_environment" and response.get("stdout"):
                try:
                    environment = json.loads(response["stdout"])
                    response = {"Python": environment.get("python", "").splitlines()[0],
                                "工具环境": environment.get("tooling_profile", "unspecified"),
                                "可用命令": [key for key, value in environment.get("binaries", {}).items() if value],
                                "可用库": [key for key, value in environment.get("optional_modules", {}).items() if value]}
                except (ValueError, TypeError, AttributeError, IndexError):
                    pass
            useful = [f"{key}: {response[key]}" for key in ("stdout", "stderr", "output", "output_preview", "preview", "message", "content") if response.get(key)]
            if useful:
                body = "\n".join(useful)
            else:
                body = json.dumps(without_evidence({key: value for key, value in response.items()
                                   if not key.endswith(("_evidence", "_sha256")) and key not in
                                   {"evidence", "artifact", "status", "exit_code", "stdout", "stderr", "observation"}}), ensure_ascii=False)
                if body == "{}":
                    body = "（无文本输出）"
        else:
            body = str(response) or "（无文本输出）"
        return header + "\n  " + clip(body)
    return None
