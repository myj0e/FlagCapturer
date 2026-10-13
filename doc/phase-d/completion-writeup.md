# Flag 完成语义与 Agent Writeup

提交 Flag 即完成对应的请求目标。平台接受结果、用户反馈和额外正确性确认不属于本项目任务的待办项。多 Flag 任务仍需处理其余实际请求目标；已经得到结果时，不为等待平台验证继续实验。

## 完成接口

`candidate_submit` 立即保存 Flag 原文及可选来源，返回 `task_status="completed"`、`subtask_completed=true` 和 `platform_verification_required=false`。提交后无需局部检查、格式匹配或平台反馈才能完成该目标。

完成解题后，agent 使用已记录的观测和步骤编写 Markdown writeup，再通过完成调用提交：

```json
{
  "outcome": "completed",
  "candidate_ids": ["candidate-1"],
  "summary": "已提交所请求的 Flag",
  "unresolved": [],
  "writeup": "# 解题记录\n\n## 题目\n...\n\n## 关键线索与步骤\n...\n\n## Flag\n..."
}
```

`completed` 要求存在当前运行的 Flag 引用且 `unresolved=[]`。`partial` 用于已提交部分 Flag、实际仍有请求目标未完成的情况，要求非空的 `unresolved`。没有得到 Flag 时可用 `unsolved` 总结尝试。兼容已有工具消费者的 `candidate_unverified` outcome 和候选 `unverified` 字段仍保留；它们不要求平台确认，不产生验证待办。新 agent 指令使用 `completed` / `partial` / `unsolved`。

`writeup` 是每次显式完成调用的必填字段，内容需为非空 Markdown，UTF-8 字节数最多 12 KiB；完整请求继续受 16 KiB 单次工具参数保护。推荐使用用户语言，包括题目概述、关键观测及来源 ID、实际解题步骤、可复现命令或代码、提交的 Flag。部分完成或未解出时记录真实剩余问题，不编造结果，也不输出私有推理。较长脚本可引用已保存的脚本/产物位置。

这些步骤在同一 agent 会话中执行。解题目标完成后保留会话供 agent 整理 writeup，成功保存后由 `run_complete` 结束运行；不会在任务结束后自动创建另一个模型会话。

## 落盘与证据

每次运行在自己的记录目录保存：

```text
runs/stage-b/<run-id>/
  events.jsonl
  artifacts/
  writeup.md
  report.md       # 用户点击 Report 后生成
```

`writeup.md` 的文本由 agent 提交，控制器负责落盘；它与根据事件生成的基础 `report.md` 分开保存。

控制器校验内容大小，以私有临时文件写入并 flush/fsync，再原子替换为权限 `0600` 的 `writeup.md`。同时保存同内容的 Markdown artifact 与 SHA-256。`writeup_saved` 事件先于 `completion_recorded`，完成记录包含文件位置和证据引用。写入失败时不提交完成状态，agent 可纠正或重试；重复完成调用不会覆盖已提交的 writeup。

若用户在 agent 编写 writeup 前停止，保留已提交的 Flag 和现有证据，不编造 writeup；历史运行没有 `writeup.md` 时也不自动补造。

## TUI 与关联功能

操作栏新增 **Writeup**。文件成功生成、运行结束后启用，点击在文件视图内渲染 Markdown，顶部显示记录路径；**Log / F2** 返回运行记录。开始或预览另一题时重置该入口，防止把上一题内容当作当前题总结。Report 继续显示运行统计和时间线，并提供 writeup 文件链接。

完成提示、Flag 窗口、初始提示词、续轮提示、压缩恢复约束、六类 workflow 的完成指导同步采用“提交完成目标，无平台验证待办”。CLI 对 `completed` 和兼容状态返回 0；`partial` / `unsolved` 返回 1。评测统计接受新增完成状态，不把提交结果等同于平台答案正确率。

## 验收范围

最终回归覆盖：Flag 提交状态、writeup 必填和 UTF-8 上限、实际文件内容/权限/哈希、保存与完成事件顺序、写入失败重试、软链接拒绝、重复完成幂等、部分完成/未解出、提前停止，以及 Writeup 标签的 Markdown 渲染和任务切换隔离。开发中不运行 smoke 测试。

2026-10-09 最终回归复验：`317 passed, 1 deselected in 119.48s`，排除原有 smoke 用例。使用自建输入、确定性模型与 Textual headless 渲染验证，未调用真实模型。验收中修正了新增 Writeup 后窄屏 Log 按钮越界的问题，Log 已前移到核心操作之后，文件标签之前。
