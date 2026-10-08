"""Bounded public solving dashboard, independent from the scrolling timeline."""
from __future__ import annotations

from typing import Any

from rich.text import Text

from ctfbot.tui.safe_text import safe_plain_text


class SolvingSummary:
    def __init__(self) -> None:
        self.snapshot: dict[str, Any] | None = None
        self.turn = 0
        self.updated_at = ""
        self.outcome = ""
        self.candidate = ""
        self.read_error = False
        self.pending_progress = False

    def replace(self, snapshot: Any, timestamp: str = "") -> bool:
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("turn"), int):
            self.read_error = True
            return False
        if not all(key in snapshot for key in ("goal", "facts", "hypotheses", "approach", "next_steps", "blockers", "corrections")):
            self.read_error = True
            return False
        self.snapshot = snapshot
        self.updated_at = timestamp
        self.pending_progress = False
        self.read_error = False
        return True

    def render(self) -> Text:
        text = Text()
        text.append("解题摘要（模型整理）\n", style="bold cyan")
        text.append("F3 隐藏 / 显示 · 可独立滚动\n\n", style="dim")
        if self.outcome:
            text.append("运行状态：" + safe_plain_text(self.outcome) + "\n", style="bold yellow")
        if self.candidate:
            text.append("最近候选：" + safe_plain_text(self.candidate)[:200] + ("…" if len(safe_plain_text(self.candidate)) > 200 else "") + "\n原文与验证状态见 Flag 窗口\n", style="yellow")
        snapshot = self.snapshot
        if snapshot is None:
            text.append(f"\n当前第 {self.turn} 轮\n等待模型发布摘要。", style="dim")
        else:
            at_turn = snapshot["turn"]
            text.append(f"\n摘要 #{snapshot['revision']} · 第 {at_turn} 轮更新\n当前第 {self.turn} 轮\n", style="dim")
            if self.updated_at:
                text.append(safe_plain_text(self.updated_at) + "\n", style="dim")
            if at_turn < self.turn:
                text.append("本轮尚未更新，以下为上次摘要。\n", style="yellow")
            elif self.pending_progress:
                text.append("有新说明或工具结果，摘要尚未更新。\n", style="yellow")

            def section(title: str, values: list[Any], style: str = "") -> None:
                text.append("\n" + title + "\n", style="bold " + style if style else "bold")
                for value in values or ["暂无"]:
                    text.append("• " + safe_plain_text(value) + "\n", style=style)

            section("当前目标", [snapshot["goal"]])
            facts = []
            for fact in snapshot["facts"]:
                ids = fact.get("observation_ids", [])
                source = ", ".join(ids) if ids else "未绑定证据"
                facts.append(f"{fact['text']} [{source}]")
            section("已确认线索（模型报告）", facts, "green")
            section("待验证假设", snapshot["hypotheses"], "yellow")
            section("当前方案与公开依据", [snapshot["approach"]])
            section("下一步", snapshot["next_steps"], "cyan")
            section("阻塞点", snapshot["blockers"], "yellow")
            if snapshot["corrections"]:
                section("更正 / 撤回", snapshot["corrections"], "magenta")
        if self.read_error:
            text.append("\n摘要证据不可用，保留上一份可读摘要。", style="red")
        return text
