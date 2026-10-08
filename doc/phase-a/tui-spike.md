# 阶段 A8：TUI 技术 spike 记录

状态：TUI 可行性 spike 验收完成；阶段 A 首版界面选用 Textual。生产 UI 和真实 runtime/session 集成仍属后续工作。

## 候选与判断

阶段 A 首版选择 Textual，prompt_toolkit 保留为未选备选。该结论基于 fake-event、resize、输入/焦点、安全文本与真实 PTY 的 spike；生产 UI 和真实 runtime session 集成仍待实现。

| 能力 | Textual | prompt_toolkit | 对 ctfbot 的影响 |
|---|---|---|---|
| 运行中后台任务 | Worker API 支持 async/worker 并让 UI 继续响应 | 提供异步及 full-screen 应用能力 | 模型、runtime 是长任务；Textual 提供较直接的 worker/message 模型。 |
| 多面板和页面 | App / Screen / Widget / layout 组合 | 全屏容器和布局可实现 | ctfbot 需要时间线、预算、活动假设、证据、session 输入输出。Textual 组件模型较贴近产品。 |
| 键盘操作 | actions/bindings/widgets | 强交互输入、键绑定和终端控制 | 两者均可用；需重点检查 TUI 与 GDB/nc 子会话的键盘焦点和终端 raw mode。 |
| 窄屏/resize | 布局响应式，可用 Pilot 模拟 resize | 需实现和验证自己的布局逻辑 | 当前产品需针对 80x24 和窄终端退化布局；候选技术提供可测试入口。 |
| 安全渲染 | Rich renderable/UI widget；仍需把外部数据当纯文本并过滤控制码 | 自定义应用输出；同样必须自行做安全文本适配 | 不可信 challenge/tool output 不允许 ANSI/OSC 直接写到终端。 |
| 依赖与稳定性 | 第三方依赖，版本锁定后纳入 | 第三方依赖，较低层/更可控 | 需评估 Python 支持、terminal compatibility 和打包维护成本。 |

官方 Textual 文档提供 Worker、Screen/layout 与 Pilot，Pilot 可模拟按键和 terminal resize；`run_test()` 的 headless app 驱动机制适合 fake provider/event stream。prompt_toolkit 也明确支持 full-screen apps；最后选择依据应是 spike 实测，而非功能清单。

## Prototype 验收表

| 检查 | 当前结果 | 要求 |
|---|---|---|
| fake event stream 持续更新 | 通过；Textual Pilot 收到全部 7 个合成事件 | 自动检查事件持续写入，未测突发、丢失或并行任务负载。 |
| 终端 resize | 通过 headless Pilot：80×24、100×30、52×14、恢复 80×24 | 各尺寸布局保持有效，日志和焦点控件仍存在；真实终端 resize 只在后续 runtime 联调时再复核。 |
| 键盘焦点 | 通过；启动时 Input 聚焦，resize 后仍聚焦，输入 `ok` 后写入 fake event | Ctrl+Q 可退出；真实 session 焦点切换仍待 runtime 联调。 |
| 长输出 | 通过；3840 字符合成输出可滚动到顶部和底部 | 此原型不提供 artifact 导出/截断提示，需由正式 event/evidence UI 设计。 |
| interactive session 形态 | fake input 提交通过 | 仅验证本地 UI 事件流，不执行宿主命令；真实 PTY/session 仍待 runtime 联调。 |
| no-color / plain mode | 通过；headless 验证 `NO_COLOR`，80×24 PTY 启动无 SGR 颜色序列 | Textual 默认 `NO_COLOR` 路径会输出灰阶 SGR；spike 显式启用 `ansi_color` 以走 `NoColor` 过滤。 |
| 不可信文本 | 通过；ESC、BEL、双向控制、换行和制表符在写入 RichLog 前可见转义，markup 关闭 | 验证的是终端控制字符不能作为控制序列输出；尚未覆盖所有终端模拟器、剪贴板或所有 Unicode 混淆字符。 |

## 本轮复现记录

- 环境：Python 3.14.4、Textual 8.2.8，依赖只安装在 `/tmp/ctfbot-phase-a-tui`，未加入项目依赖。
- 自动检查：`/tmp/ctfbot-phase-a-tui/bin/python doc/phase-a/spikes/tui_textual/verify.py` 通过；覆盖 `NO_COLOR`、输入焦点、长日志滚动、三种终端尺寸及恢复、安全文本显示。
- PTY：`NO_COLOR=1 TERM=xterm-256color .../python doc/phase-a/spikes/tui_textual/app.py` 在 80×24 PTY 启动，Ctrl+Q 退出；输出未包含 SGR 前景/背景颜色序列。
- 自动检查脚本：[verify.py](spikes/tui_textual/verify.py)。

## TUI 与业务逻辑边界

TUI 只订阅 application service 的 run state/event stream，并提交用户动作（pause/resume/cancel/hint/session input）。TUI 不直接调用模型 SDK、shell、Docker 或验证器。Application service 没有运行中的 UI 时也能驱动同一 run；退出和取消语义由 application/runtime 定义并写 event log。

基于本次 fake-event/PTY 结果，阶段 A 的 TUI 技术选型采用 Textual。这个结论只确定首版 UI 框架，不表示正式 run 工作台、artifact 浏览或真实交互 session 已实现。

终端控件、鼠标交互、丰富主题和多窗口美化不是本 spike 目标；先验证观察/控制解题运行需要的功能。
