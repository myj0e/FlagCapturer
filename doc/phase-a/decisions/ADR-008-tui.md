# ADR-008：TUI 技术

- 状态：已决定；阶段 A 首版选择 Textual，生产工作台功能仍需实现
- 日期：2026-09-29

## 背景

ctfbot 的主界面是持续运行的解题工作区：时间线、预算、活动假设、证据查看、交互会话和取消操作需要同时呈现。屏幕 resize、键盘焦点、长输出和不可信内容安全渲染是首版要求。

## 候选与当前判断

Textual 是首选 spike 候选，prompt_toolkit 是备用。Textual 官方文档提供 async Worker、Widget/Screen/layout、Pilot key input 与 resize 模拟；适合尽快验证事件工作台。prompt_toolkit 的 full-screen application 能力也足够实现，若 Textual 依赖/终端兼容性问题显著，可切换。

## 决策

在 `doc/phase-a/spikes/tui_textual` 内隔离 fake-event prototype。Textual 8.2.8 的 Pilot 验证了合成事件更新、80×24/100×30/52×14 resize、输入焦点、长输出滚动和不可信文本转义；80×24 PTY 验证了 `NO_COLOR=1` 启动及 Ctrl+Q 退出。选择 Textual 作为首版 TUI 框架。当前 spike 依赖只放在 `/tmp`，正式 UI 实现时再把锁定版本加入项目依赖。

## 复审条件

- 运行中事件更新不会阻塞输入/取消操作。
- 80x24、窄屏和动态 resize 可读、可恢复；焦点明确且 GDB/nc 会话不会抢错快捷键。
- 长输出可以分页查看，截断摘要链接到 raw artifact。
- no-color 模式功能完整。
- 不可信 ANSI/OSC/C0/C1/双向文本不能控制宿主终端、伪造可信事件或形成误导链接。
- TUI 不掌握编排/执行策略；headless 与 TUI 调同一 application service。

阶段 A spike 未覆盖真实 runtime/PTTY session 焦点、artifact 预览/截断、负载下事件响应，以及多种真实终端模拟器。这些是正式 UI 和 runtime 联调的验收项，不阻止本 ADR 对框架作出选择。
