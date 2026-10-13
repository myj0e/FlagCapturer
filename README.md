# FlagCapturer（ctfbot）

> A tool-driven CTF solving workbench for LLM agents.

FlagCapturer（命令名和 Python 包名为 `ctfbot`）旨在把 CTF 选手的分析、实验和验证流程组织成可观察、可复现的工具调用循环。计划以键盘优先的终端界面（TUI）作为主要交付形态，并按题型按需加载隐写、密码学、数字取证、Web、Pwn、Reverse 等工具能力。

## 项目状态

当前版本 `0.1.0` 提供单题 CLI/TUI、可手动停止的单 agent、Docker 工具沙箱、证据与报告、六类工作流、候选流程评测和命令回放。真实解题需要配置 Codex 模型，并逐题授权附件及工具输出的模型传输。

程序记录模型提交的 Flag 及证据，不内置已知答案验证。`candidate_submit` 完成对应请求目标；多子题任务继续处理剩余要求，全部完成后提交 writeup 并通过 `run_complete` 结束为 `completed`。仍有实际未完成要求时使用 `partial`，没有找到 Flag 时使用 `unsolved`。平台确认不属于任务待办，局部校验、模型说明和命令成功均不表示答案正确。

开发历史保留在 [阶段 A](doc/phase-a/README.md)、[阶段 C](doc/phase-c/README.md)和[阶段 D](doc/phase-d/README.md)。其中早期精确答案验证和 Stage A pilot 原型已退役，当前接口与迁移说明见[代码整理说明](doc/code-cleanup.md)。

长运行可通过 `state_read` 找回摘要历史、失败实验、脚本、会话和显式导出的检查点。上下文占用超过窗口的 80% 时，控制器自动生成有界状态快照、替换模型会话并继续任务，不调用 LLM 摘要或特定 provider 压缩 API；全部原始证据保留，遗漏记录按需读取。底部状态行动态显示上下文占用与窗口容量，估计标注 ≈，不把累计账单用量当作当前占用。容量未知时等待标准用量事件，不猜测窗口。实现与边界见[自动上下文压缩](doc/phase-d/automatic-context-compaction.md)。

## 快速开始

要求 Python 3.11 或更新版本：

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .

ctfbot --version
ctfbot doctor
ctfbot
```

`ctfbot doctor` 显示基线环境状态；`ctfbot baseline-smoke` 运行不联网、不触碰题目数据的合成闭环。`ctfbot solve` 与 `ctfbot evaluate` 需要不可变工具镜像、逐题模型传输授权，并且必须显式传 `--confirm-model-usage` 才会使用模型额度。合成 smoke 不调用模型。

### 使用 TUI 工作台

先完成 Codex 模型配置（`ctfbot llm setup`），再运行 `ctfbot`。**Challenge file / folder 可直接填单个可执行文件/附件路径，或仅含一个附件的普通目录**，也接受 importer 生成的私有 workspace。单文件在 Preview 时复制到 `data/imports/` 的私有快照并记录哈希，原文件保持不变；勾选 **File model transfer** 表示允许将该文件及工具输出发送给配置的模型，随后 Preview → Run。许可用 `[ ] 未允许 / OFF` 和 `[x] 已允许 / ON` 区分，不依赖颜色。未勾选可预览但不可运行；该选项不修改已有 workspace 的授权。导入仅读取文件，不在宿主机执行；单文件大小限制为 1 字节至 32 MiB。

界面采用深色工作台配色：终端宽度达到 120 列时，左侧为题目配置与预览，右侧为操作、状态和运行记录；更窄时自动切换上下布局。短终端中的配置区可滚动，操作栏可横向滚动，Tab 可逐项聚焦。独立指标栏显示运行阶段、轮次、工具次数和耗时。

路径输入框保留原始文件/目录，勾选许可不会改写路径；Preview 后在“题目预览”中查看实际运行使用的 Snapshot。三个输出区域分别为：**状态与操作提示**（成功、失败、阻断原因及下一步），**题目预览**（原始路径、快照、附件、授权、验证方式，可滚动），**运行记录**（运行时模型与工具事件、候选 flag）。运行结束后，点击 **Evidence** 切换到 `events.jsonl` 文件视图，按每页 25 条展示格式化、高亮的 JSON，使用上一页 / 下一页浏览；无效或不完整的 JSON 行保留原文并标注。点击 **Report** 生成 `report.md` 并直接渲染；点击 **Writeup** 渲染 agent 完成任务时生成并保存在本次记录目录的 `writeup.md`，文件路径显示在视图顶部。点击 **Log** 或按 **F2** 返回运行记录；文件视图切换不会改动运行统计和解题状态。

运行记录按轮次分区：模型的公开说明单独标注；工具调用与返回合并为卡片，显示名称、参数摘要、状态、耗时和返回摘要；flag 候选、错误和完成状态有独立标记。顶部状态栏固定显示 Run 标识、轮次、工具调用计数与运行时长。普通生命周期、hash 和 artifact 路径不刷屏；长输出截断，完整 `events.jsonl` 与 artifacts 仍保存。不展示或推断隐藏推理。通过滚轮、键盘或滚动条查看历史会暂停自动跟随，出现“查看最新”按钮；新消息不会打断阅读，只有点击该按钮才恢复跟随。Run 时自动折叠配置与预览，把记录框放大；Evidence 单独展示原始事件 JSON。按 **F2** 或 **Config / Log** 切换，Preview 会恢复配置。

底部 statusline 显示本次运行累计 Token（输入 / 输出）与上下文占用，自动按 K=1,000、M=1,000,000 缩放；例如 `Token 1.2M（入 1.1M / 出 100K） · 上下文≈60K / 200K`。累计 token 不等于当前上下文占用，压缩不会清零累计用量；provider 未提供数据时显示 `—`。用量只作参考，不自动终止任务，超量时可手动 Stop。

运行时记录区按约 75:25 分为左右两栏：右侧显示模型整理的目标、线索及证据编号、待验证假设、方案、下一步、阻塞点和更正。模型通过 `summary_update` 在取得重要结果或新公开说明后刷新完整摘要，不另发模型总结请求；每份摘要标注版本、轮次和时间，有新进展但尚未刷新时提示摘要可能过时。按 **F3** 隐藏或显示右栏；终端宽度小于 110 列时改为上下排列，两栏独立滚动。结束后保留摘要和最近候选，Evidence 回看恢复摘要更新记录；旧日志没有摘要时显示等待状态。摘要是模型报告，引用证据并不等于证明结论正确。

长脚本可使用 `script_save → script_start → session_read`：后台执行不受普通命令的 120 秒限制，模型根据进度、运行时长与无输出时长决定继续等待或 `session_close`。每个后台脚本仍有最多 30 分钟的单次执行保护与输出上限，脚本结束或超时不会因为总任务额度而结束解题。整次任务没有轮次、总时长或 token 额度限制。提示词要求定期输出进度并保存检查点；普通命令超时会尽量保留沙箱，必须关闭沙箱时立即终止本次解题。详见 [命令超时与长任务脚本](doc/phase-d/command-timeouts.md)。

Flag 由 agent 根据任务、线索和工具结果识别并提交原文，不执行格式或平台接受检查。提交即完成对应目标；多子题或多 Flag 任务继续处理实际剩余要求，不把平台确认列为待办，也不额外发明验证任务。`candidate_submit` 可附带 `subtask` 标签。所有请求的 Flag 提交后，agent 编写 Markdown writeup，再调用 `run_complete(outcome="completed", candidate_ids=[...], unresolved=[], writeup=...)`。实际仍有子任务未完成时使用 `partial` 并列明剩余要求，没有找到 Flag 时使用 `unsolved`。每次显式结束都要求 agent 提交 writeup，控制器先保存到该次运行目录的 `writeup.md`，再结束任务；工具历史中的 `unverified` / `candidate_unverified` 字段保留兼容，不代表平台确认待办。详细约定见[任务完成与 Writeup](doc/phase-d/completion-writeup.md)。

运行镜像自动填写，私有输出默认 `runs/stage-b`。点击 **Preview** 检查附件 hash、模型传输授权、验证方式和单次工具保护，然后 **Run**；未获题目授权或输入不符合准入策略时仍阻止启动。**Stop** 请求终止模型和 sandbox；运行中即可点击 **Flag · 数量** 打开候选窗口（尚未提交时显示空状态），选择答案并点击“复制 flag”；新增候选立即更新列表，保留当前选择、选区和滚动位置，运行结束或停止后仍可查看。提交完成对应目标，运行在 writeup 保存后结束；多子题继续剩余要求，数量仅表示已提交记录数；候选文本框也支持选中后 Ctrl+C 复制（只读，不接受粘贴）。复制按钮保留完整原文，不增加 Python 依赖：Windows 使用 Win32 剪贴板，macOS 使用系统自带 pbcopy，Linux 使用现有桌面接口（可用的 wl-copy / xclip / xsel，或原生 X11）；SSH 使用终端 OSC 52。界面区分系统写入成功与尚未确认的终端请求。纯 Wayland 且没有可用桌面接口时需要终端允许 OSC 52；原生 X11 复制在应用退出后的保留取决于桌面剪贴板管理器。窗口可重复打开，**Evidence** 显示候选及状态，**Report** 生成 `report.md`，报告不复制原始候选。TUI 的“补充提示词（可选）”支持多行编辑与粘贴，可填写解题线索和已知 flag 格式；Enter 换行，Tab 切换到下一项，选中后 Ctrl+C 复制，Ctrl+V 粘贴系统剪贴板，长内容可在框内滚动，留空即可运行。修改后需重新 Preview，换行会保留到实际解题提示词中。系统剪贴板读取不可用时使用应用内已复制的文本；SSH 下请使用终端原生粘贴（如 Ctrl+Shift+V / Cmd+V），不会读取服务器桌面剪贴板。`solve` 通过 `--additional-prompt 'Flag 前缀为 SUCTF'` 提供提示。候选由 LLM 判断并提交，不执行格式检查；`completed`（以及兼容历史状态 `candidate_unverified`）退出码为 0，表示 Flag 已提交并完成任务；`partial`、`unsolved` 或错误退出为 1。`evaluate` 使用无答案文件的 v3 数据集，统计候选记录、显式结束、错误、token 用量和证据，不衡量答案正确率。

TUI 启动后会在后台查找本机 **`ctfbot-tools:candidate`（general-v3）**，将其解析为不可变 `sha256` image ID 并自动填入 **Runtime (automatic)**。正常使用无需复制镜像 ID；该字段保留手动覆盖。镜像预装 Python 解题库、GDB/binutils、32/64 位 GCC/G++、常用归档/取证工具，以及 Node、Ruby、Perl、Java 环境；完整清单、构建和验收步骤见[通用工具镜像](doc/phase-d/tool-image.md)。未找到镜像或 Docker 不可用时显示准备提示；准备完成后再次 Preview 会重试查找。查找只执行本地 image inspect，不拉取镜像、不启动容器或模型。原最小 fixture 镜像保留供固定服务 profile 使用。

TUI 可预览 C3 合成服务快照的 endpoint 和运行阻断原因；本地服务仅在受审 profile 匹配时可运行，远端题仅在 C4 完整验收记录、独立 grant 和受审 profile 匹配时可运行；本机尚未配置远端 profile。题目描述不能启用网络。模型传输授权由 workspace provenance 中该题的 `model_data_authorized` 和非空授权依据决定；再次运行也会重新检查 admission、路径、附件 hash 和不可变 image。

### 受审本地服务

`ctfbot service status` 查看 profile 与待恢复记录；`ctfbot service approve --acceptance <私有验收目录>/acceptance.json --basis <审查依据>` 根据当前完整验收启用固定合成服务。Docker 请求超时或 controller 中断后使用 `ctfbot service recover`，结果未确认时禁止新服务 run。可在子命令前传 `--service-profile <私有配置路径>`，TUI 和 `solve` 共用该配置。完整操作见[C3 启用与恢复](doc/phase-c/service-activation.md)。

### 受审远端 TCP

`ctfbot remote status` 查看受审 profile 和待恢复状态，`ctfbot remote recover` 按所有权处理未完成的 solver 容器。取得并审阅匹配目标的完整 C4 验收后，使用 `ctfbot remote approve --acceptance <私有验收记录> --basis <审查依据>` 安装 profile。TUI/solve 共用 profile 和独立目标 grant；可在子命令前指定 `--remote-profile` 与 `--remote-grant`。短时自建端点已完成实机与临时受审启用验收，项目默认 profile 未安装。配置与范围见 [C4 实施记录](doc/phase-c/remote-targets.md)和 [实机结果](doc/phase-c/remote-acceptance.md)。

## 临时配置 ChatGPT/Codex 模型

需要本机安装 Codex CLI，并从仓库根目录运行：

```sh
ctfbot llm setup
ctfbot llm status
ctfbot llm test
# 实验性工具调用 smoke；执行一次会发起一个真实模型请求
ctfbot llm tool-smoke
```

`setup` 会通过 Codex App Server 发起 ChatGPT 浏览器登录（也可选设备码），读取当前账号可用模型，并把 provider、模型 ID 和 reasoning effort 保存到被 Git 忽略的 `data/llm.toml`。登录凭据继续由 Codex CLI 管理，ctfbot 不复制或保存 token。主菜单也可按 `l` 进入设置。

`test` 会发送一条短模型请求；`tool-smoke` 会发起一个真实模型 turn 并提供固定返回值的工具，二者都可能占用账号额度，只有明确运行时才会调用模型。当前连接层不替 ctfbot 解题、不访问 CTF 题目附件。阶段 A 记录说明一次工具 smoke 已执行，不应为了重复验证而自动再运行。详细说明见[Codex App Server 临时接入说明](doc/phase-a/codex-app-server-integration.md)和[OpenAI Codex App Server 文档](https://developers.openai.com/codex/app-server)。

## 设计目标

支持图片输入的模型可按需调用 `image_view` 查看 `/challenge` 原图或 `/work` 派生图，并通过 `crop: {x, y, width, height}` 查看局部区域。图片解析在沙箱内执行，适配层传送真实图像内容；文本模型不开放此工具。保留原始像素尺寸，超出单次保护时提示裁剪。接口、隔离边界与验收见[图片查看工具](doc/phase-d/image-view.md)。

- 单个主 agent 共享跨领域上下文，并依据证据选择工具；多 agent 通过后续消融实验决定是否引入。
- 让工具返回短摘要和证据引用，同时保存原始输入、输出、脚本、事件和产物。
- 通过有单次工具保护和授权边界的 sandbox 执行命令、临时脚本及交互式会话。
- 记录 Flag 的原文、来源和局部检查，提交后完成对应目标，保存 writeup 后结束任务。
- TUI 与 headless 诊断、报告、批量评测共用同一 application service。

以上是设计目标，不代表当前版本已实现这些功能。详细边界和阶段验收见设计文档。

## 仓库结构

```text
src/ctfbot/
  application/       # TUI 与 headless 命令共用的用例服务
  tui/               # 终端交互界面
  cli/               # 启动器和非交互命令
  challenge/         # manifest、附件和 run 生命周期
  agent/             # 编排和解题策略
  model_adapters/    # provider 适配
  tools/             # 通用工具和领域工具包
  runtime/           # 沙箱与交互 session
  evidence/          # 事件、事实和 artifacts
  verification/      # flag 校验
  reporting/         # 报告与复现包
  benchmark/         # 题集和评测
doc/                  # 调研、设计计划和项目基线
```

当前代码覆盖单题 TUI/headless、生命周期、交互 session、固定本地服务、受控 TCP、六类基础工作流、评测、命令回放和受审记忆；各模式的实测范围见阶段记录。真实模型评测、多机制领域扩展及更广 provider/runtime 验收仍在后续路线中。

## 开发约定

- 安装开发版：`python -m pip install -e .`。
- 本地题目、运行记录和生成产物放在 `data/`、`runs/`、`artifacts/` 或 `reports/`，这些路径已加入忽略规则。
- 凭据通过本地环境或后续秘密管理接口提供；不要把 API key、私有题目附件、flag、writeup 或运行产物提交到 Git。
- 只对获准的 CTF 题目、靶场和 benchmark 执行扫描或利用操作。

## 计划与调研

- [详细设计与实施计划](doc/AI_CTF_AGENT_DETAILED_PLAN.md)
- [总体设计计划](doc/AI_CTF_AGENT_DESIGN_PLAN.md)
- [竞品调研报告](doc/CTF_AI_COMPETITOR_RESEARCH.md)
- [项目基线记录](doc/PROJECT_BASELINE.md)
- [阶段 A 施工记录](doc/phase-a/README.md)
- [变更记录](CHANGELOG.md)

## License

本项目使用 [MIT License](LICENSE)。

### Agent 通用可靠性增量

候选记录、局部检查与结束已分开；新增来源绑定、原始证据聚焦读取、公开假设/实验记录和只读诊断。使用 `ctfbot diagnose-run --run-dir <运行目录>` 查看实际 prompt、工具返回覆盖范围和结束依据的私有引用。实施范围、兼容变化与验收见[通用可靠性实施记录](doc/phase-d/agent-common-improvement-implementation.md)。

TUI、solve 和 evaluate 的工具调用次数和累计工具回复字节数仅作统计，不设累计额度，也不会在使用达到 80% 时缩短回复或拒绝调用。已移除 `--max-tool-calls` 和 `--max-total-tool-calls` 参数。单次工具回复仍限制为最多 16 KiB；完整输出保存在证据文件中，可分段读取。整次任务不设轮次、总时长或 token 额度，不会因为达到额度而自动结束；可随时通过 Stop 手动停止。已移除 solve 的 `--max-turns` / `--wall-time` 和 evaluate 的 `--max-total-turns` / `--max-total-wall-time` 参数；回放的单次超时保护仍保留。长任务可用 `session_read collect_seconds` 合并观察，聚焦读取返回的 `source_ref` 可简化实验绑定。

多子题 Flag 的逐步提交、运行中查看与多候选结束汇总见[实施记录](doc/phase-d/multi-flag-progress-implementation.md)。真实模型是否及时提交仍需依据运行日志观察。

任务额度取消、累计 Token 与上下文 statusline 的统计口径见[额度与用量显示修改](doc/phase-d/unlimited-task-usage-implementation.md)。

Sandbox general-v3 补齐历史缺失的 UPX、Go、VeraCrypt、cryptsetup，并验收 objdump / factor；构建、清理和关联 workflow 变更见 [实施记录](doc/phase-d/sandbox-tools-v3.md)。
