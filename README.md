# FlagCapturer（ctfbot）

> A tool-driven CTF solving workbench for LLM agents.

FlagCapturer（命令名和 Python 包名为 `ctfbot`）旨在把 CTF 选手的分析、实验和验证流程组织成可观察、可复现的工具调用循环。计划以键盘优先的终端界面（TUI）作为主要交付形态，并按题型按需加载隐写、密码学、数字取证、Web、Pwn、Reverse 等工具能力。

## 项目状态

当前版本 `0.1.0` 提供单题 CLI/TUI、受预算约束的单 agent、Docker 工具沙箱、证据与报告、六类工作流、候选流程评测和命令回放。真实解题需要配置 Codex 模型，并逐题授权附件及工具输出的模型传输。

程序记录模型选出的 flag 候选及证据，不内置已知答案验证。用户在比赛平台确认候选；局部校验、模型说明和命令成功均不表示答案正确。`candidate_submit` 不结束运行，模型可继续检查，再通过 `run_complete` 结束为 `candidate_unverified` 或 `unsolved`。

开发历史保留在 [阶段 A](doc/phase-a/README.md)、[阶段 C](doc/phase-c/README.md)和[阶段 D](doc/phase-d/README.md)。其中早期精确答案验证和 Stage A pilot 原型已退役，当前接口与迁移说明见[代码整理说明](doc/code-cleanup.md)。

长运行可通过 `state_read` 找回摘要历史、失败实验、脚本、会话和显式导出的检查点。续轮自动提供有界状态快照，长工具输出提供头尾预览与原始证据定位。普通回复、状态恢复和收尾各有独立额度，字节额度与模型上下文占用分开记录。观测到 Codex 压缩完成时会补发状态；当前窗口占用缺少可靠计数时显示未知。实现边界与验收见[上下文管理实施记录](doc/phase-d/agent-context-management-implementation.md)。

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

### 使用最小 TUI

先完成 Codex 模型配置（`ctfbot llm setup`），再运行 `ctfbot`。**Challenge file / folder 可直接填单个可执行文件/附件路径，或仅含一个附件的普通目录**，也接受 importer 生成的私有 workspace。单文件在 Preview 时复制到 `data/imports/` 的私有快照并记录哈希，原文件保持不变；勾选 **File model transfer** 表示允许将该文件及工具输出发送给配置的模型，随后 Preview → Run。许可用 `[ ] 未允许 / OFF` 和 `[x] 已允许 / ON` 区分，不依赖颜色。未勾选可预览但不可运行；该选项不修改已有 workspace 的授权。导入仅读取文件，不在宿主机执行；单文件大小限制为 1 字节至 32 MiB。

路径输入框保留原始文件/目录，勾选许可不会改写路径；Preview 后在“题目预览”中查看实际运行使用的 Snapshot。三个输出区域分别为：**状态与操作提示**（成功、失败、阻断原因及下一步），**题目预览**（原始路径、快照、附件、授权、验证方式，可滚动），**运行记录 / Evidence**（运行时模型与工具事件、候选 flag；Evidence 按钮回看记录）。Report 生成独立报告文件，并在状态栏显示路径。

运行记录按轮次分区：模型的公开说明单独标注；工具调用与返回合并为卡片，显示名称、参数摘要、状态、耗时和返回摘要；flag 候选、错误和完成状态有独立标记。顶部状态栏固定显示 Run 标识、轮次、工具调用计数与运行时长。普通生命周期、hash 和 artifact 路径不刷屏；长输出截断，完整 `events.jsonl` 与 artifacts 仍保存。不展示或推断隐藏推理。向上滚动会暂停自动跟随，出现“有新动态 · 返回最新”按钮；回到底部恢复跟随。Run 时自动折叠配置与预览，把记录框放大；Evidence 使用同一分组规则回看。按 **F2** 或 **Config / Log** 切换，Preview 会恢复配置。

运行时记录区按约 75:25 分为左右两栏：右侧显示模型整理的目标、线索及证据编号、待验证假设、方案、下一步、阻塞点和更正。模型通过 `summary_update` 在取得重要结果或新公开说明后刷新完整摘要，不另发模型总结请求；每份摘要标注版本、轮次和时间，有新进展但尚未刷新时提示摘要可能过时。按 **F3** 隐藏或显示右栏；终端宽度小于 110 列时改为上下排列，两栏独立滚动。结束后保留摘要和最近候选，Evidence 回看恢复摘要更新记录；旧日志没有摘要时显示等待状态。摘要是模型报告，引用证据并不等于证明结论正确。

长脚本可使用 `script_save → script_start → session_read`：后台执行不受普通命令的 120 秒限制，模型根据进度、运行时长与无输出时长决定继续等待或 `session_close`。仍受整次解题预算（默认最多 30 分钟）和输出上限约束。提示词要求定期输出进度并保存检查点；普通命令超时会尽量保留沙箱，必须关闭沙箱时立即终止本次解题。详见 [命令超时与长任务脚本](doc/phase-d/command-timeouts.md)。

补充提示词可填写线索、已知 flag 格式或解题偏好，也可留空。模型结合证据识别候选并提交原文，不执行格式检查。候选状态为 `unverified`，模型可继续做局部检查；显式结束时结果为 `candidate_unverified`，候选需由你在比赛平台确认。证据不足时可明确以 `unsolved` 结束。

运行镜像自动填写，私有输出默认 `runs/stage-b`。点击 **Preview** 检查附件 hash、模型传输授权、验证方式和预算，然后 **Run**；未获题目授权或输入不符合准入策略时仍阻止启动。**Stop** 请求终止模型和 sandbox；完成后点击 **Flag** 打开候选窗口，选择答案并点击“复制 flag”；复制保留完整原文，通过终端 OSC 52 写入剪贴板（终端需允许此功能）。窗口可重复打开，**Evidence** 显示候选及状态，**Report** 生成 `report.md`，报告不复制原始候选。TUI 的“补充提示词（可选）”可填写解题线索和已知 flag 格式，留空即可运行。`solve` 通过 `--additional-prompt 'Flag 前缀为 SUCTF'` 提供提示。候选由 LLM 判断并提交，不执行格式检查；`candidate_unverified` 的退出码为 0 表示已结束并记录候选，不证明答案正确；`unsolved`、预算耗尽或错误退出为 1。`evaluate` 使用无答案文件的 v3 数据集，统计候选记录、显式结束、错误、预算和证据，不衡量答案正确率。

TUI 启动后会在后台查找本机 **`ctfbot-tools:candidate`（general-v2）**，将其解析为不可变 `sha256` image ID 并自动填入 **Runtime (automatic)**。正常使用无需复制镜像 ID；该字段保留手动覆盖。镜像预装 Python 解题库、GDB/binutils、32/64 位 GCC/G++、常用归档/取证工具，以及 Node、Ruby、Perl、Java 环境；完整清单、构建和验收步骤见[通用工具镜像](doc/phase-d/tool-image.md)。未找到镜像或 Docker 不可用时显示准备提示；准备完成后再次 Preview 会重试查找。查找只执行本地 image inspect，不拉取镜像、不启动容器或模型。原最小 fixture 镜像保留供固定服务 profile 使用。

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

- 单个主 agent 共享跨领域上下文，并依据证据选择工具；多 agent 通过后续消融实验决定是否引入。
- 让工具返回短摘要和证据引用，同时保存原始输入、输出、脚本、事件和产物。
- 通过有预算和授权边界的 sandbox 执行命令、临时脚本及交互式会话。
- 记录 flag 候选的原文、来源和局部检查，最终由用户在比赛平台确认。
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

日常 TUI/solve 默认只统计工具调用次数，不设次数上限；时间、轮次和输出预算仍有效。显式 `--max-tool-calls` 与受控 `evaluate` 的整批额度可用于固定预算评测。长任务可用 `session_read collect_seconds` 合并观察，聚焦读取返回的 `source_ref` 可简化实验绑定。
