# FlagCapturer（ctfbot）

> A tool-driven CTF solving workbench for LLM agents.

FlagCapturer（命令名和 Python 包名为 `ctfbot`）旨在把 CTF 选手的分析、实验和验证流程组织成可观察、可复现的工具调用循环。计划以键盘优先的终端界面（TUI）作为主要交付形态，并按题型按需加载隐写、密码学、数字取证、Web、Pwn、Reverse 等工具能力。

## 项目状态

当前版本 `0.1.0` 是工程基线，不是可自动解题的完整 agent。现在包含 Python 包结构、版本命令、环境诊断和最小终端菜单；LLM 调用、题目导入、沙箱执行、工具包和解题闭环尚未实现。

TUI 框架、模型 provider 和 sandbox/runtime 后端仍待阶段 A 评估。当前源码不依赖运行时第三方库，避免在可行性验证前绑定 UI 或 agent 框架。

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

`ctfbot doctor` 显示基线环境状态；`ctfbot` 打开当前的导航菜单。暂时没有 CTF 解题命令。无交互终端时可运行 `python -m ctfbot doctor`。

## 设计目标

- 单个主 agent 共享跨领域上下文，并依据证据选择工具；多 agent 通过后续消融实验决定是否引入。
- 让工具返回短摘要和证据引用，同时保存原始输入、输出、脚本、事件和产物。
- 通过有预算和授权边界的 sandbox 执行命令、临时脚本及交互式会话。
- 区分 flag 候选、格式命中和经过 oracle/validator 验证的结果。
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

目前功能实现集中在 `ctfbot --version`、`ctfbot doctor` 和基础菜单；其余目录先确立模块边界，后续按计划逐步实现。

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
- [变更记录](CHANGELOG.md)

## License

本项目使用 [MIT License](LICENSE)。
