# TUI 精简运行记录

2026-10-08：每个 agent turn 只显示公开模型说明、工具调用参数摘要、返回摘要。
后台 metadata、正常生命周期、命令检查点、工作流读取审计和 artifact hash 不作为
独立日志行显示；失败、停止、清理异常与最终结果仍显示，完整 evidence 不删除。

- 模型公开说明通过现有 Codex `item/completed` 的 `agentMessage` 接入；不用私有
  reasoning item，也不把 delta 和 completed 重复展示。来源参照
  [OpenAI 官方 App Server 文档](https://learn.chatgpt.com/docs/app-server)。
- observer 是可选接口；未实现 observer 的 provider 在 turn 返回后记录公开文本。
  即使 turn 已产生验证/格式候选，也保存该文本；没有说明则明确显示未提供。
- 工具参数只显示命令/关键参数，保存脚本显示路径和长度。返回展示 stdout/stderr
  等有效内容及状态、退出码、耗时；工作流发现、读取和环境探测使用简短摘要。
- 参数最多约 260 字符/2 行，说明与返回最多约 900 字符/6 行。原文在私有 evidence。
  UI 只读取路径受限、权限检查、大小检查及 SHA-256 校验通过的 artifact，按纯文本
  转义终端控制字符。候选沿用 4 KiB 上限与既有校验。
- Run 自动折叠配置/预览，结束后保持大记录视图；F2 或 Config/Log 可切换，Preview
  恢复配置。Evidence 回看使用相同过滤与摘要，不改写原始记录。

验收：`tests/test_tui_timeline.py` 覆盖顺序、去重、返回内容、脚本路径、长输出、
控制字符、展开高度、F2、回看一致性及 mock Codex observer；既有协议测试检查只
转发公开消息，忽略 reasoning。实机单 ELF / 无 oracle / 自动 general-v2 镜像 / 报告 /
回放与清理通过，记录位于 `data/acceptance/20261008-timeline-tui-1/acceptance.json`。
未调用真实模型、未运行用户私有附件。
