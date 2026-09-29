# ctfbot 项目基线

状态：基础工程骨架已建立；CTF 解题闭环尚未实现。

远程仓库：[myj0e/FlagCapturer](https://github.com/myj0e/FlagCapturer)，默认分支为 `main`。

## 已确立的基线

- 使用 Python 3.11+ 和 `src/` 布局；安装元数据与 `ctfbot` 命令入口由 `pyproject.toml` 管理。
- 当前运行代码只依赖 Python 标准库。TUI 框架、LLM provider、容器/session backend 均保留为阶段 A 的选型项。
- `ctfbot` 打开最小交互菜单；`ctfbot doctor` 和 `ctfbot --version` 可在无解题后端时运行。
- TUI 与 headless 命令共享 application service 边界；后续不得在两个入口复制业务状态机或安全策略。
- 本地题目和运行数据默认放在 `data/`，由 `.gitignore` 排除。凭据不得写入题目目录或提交到仓库。
- 源码包边界对应详细设计计划：`application`、`tui`、`cli`、`challenge`、`agent`、`model_adapters`、`tools`、`runtime`、`evidence`、`verification`、`reporting`、`benchmark`。

## 本地使用

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
ctfbot --version
ctfbot doctor
ctfbot
```

## 当前不包含

当前基线不启动容器、不访问模型 API、不导入题目、不执行题目附件，也不宣称支持解题。交互菜单仅用于确认包安装与导航入口已就位。

## 后续基线任务

1. 阶段 A 确认 TUI、模型 adapter 和 sandbox/runtime 的技术选型并记录 ADR。
2. 固化 v1 challenge manifest、run state 和 append-only event schema。
3. 实现 fake provider 驱动的单题状态循环与 evidence store。
4. 选定 sandbox adapter 后再开放命令、脚本与交互 session 工具。
5. 通过对照评测建立 pilot 数据基线，再扩展领域工具包。

详细范围、数据契约和验收条件见[详细设计与实施计划](AI_CTF_AGENT_DETAILED_PLAN.md)。
