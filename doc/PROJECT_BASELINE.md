# ctfbot 项目基线

状态：基础工程与 A 阶段 headless baseline 代码已建立；10 道 pilot 已正式准入为私有本地数据，Docker runtime 负面验收和真实解题基线未完成。

远程仓库：[myj0e/FlagCapturer](https://github.com/myj0e/FlagCapturer)，默认分支为 `main`。

## 已确立的基线

- 使用 Python 3.11+ 和 `src/` 布局；安装元数据与 `ctfbot` 命令入口由 `pyproject.toml` 管理。
- 当前运行代码只依赖 Python 标准库；Textual spike 不属于生产依赖。Codex App Server 有临时 adapter，用于 ChatGPT 登录、模型选择、状态查询、文本 smoke 和 experimental dynamic-tool smoke。
- `ctfbot` 打开最小交互菜单；`doctor`、`llm setup/status/test/tool-smoke`、离线 `baseline-smoke`、以及显式授权的 `solve`/批量 `baseline` 命令可从命令行调用。真实模型 smoke 与 solve 会消耗额度；当前数据准入检查会阻止未审题目运行。
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
ctfbot llm setup
ctfbot llm status
# 显式调用模型并可能消耗额度
ctfbot llm test
# 一次实验性工具调用；每次会发起模型请求
ctfbot llm tool-smoke
ctfbot
```

## 当前不包含

当前没有启动过挑战服务或进行真实解题，也没有解题结果。Stage A 已实现 controller/tool loop、JSONL evidence、oracle verifier、临时 Codex dynamic-tool adapter、digest 固定的 Docker runtime 与批量评测 runner；Docker adapter 已通过本机 synthetic live smoke 的基本命令、只读挂载、资源配置、超时终止和清理检查，agent/provider loop 通过 fake/synthetic 验证。10 道 CTFTiny 题目已正式准入为私有本地 pilot，快照在项目仓库外；题目数据仍未获准传给模型。这不能视作恶意镜像隔离认证或真实 provider/CTF 解题验收。

## 后续基线任务

1. A4 已完成：10 道私有本地 pilot 正式准入并生成仓库外快照；不分发题目，也未授权模型数据传输。
2. 完成 Docker runtime 负面边界验收；EnIGMA 仅作为源码静态参考，不纳入阶段 A 运行对照。
3. 对单题 smoke 单独申请题目数据传输和模型调用授权，再运行 pilot baseline 与三题复跑。
4. 当前 Codex App Server 只作为临时 ChatGPT adapter；新增/选定正式 provider 时，用同一 `ModelSession` 契约做真实 usage、取消、错误和版本兼容验证。
5. 阶段 A 通过后，再进入阶段 B 的正式 TUI 工作台、领域工具与更完整的 run lifecycle。

详细范围、数据契约和验收条件见[详细设计与实施计划](AI_CTF_AGENT_DETAILED_PLAN.md)。

阶段 A 当前进度和阻塞条件见[阶段 A 施工记录](phase-a/README.md)。临时 ChatGPT/Codex 接入细节见[Codex App Server 说明](phase-a/codex-app-server-integration.md)。普通 workspace sandbox 不可访问 Docker socket；经批准的 synthetic 容器 runtime smoke 已检查文件、资源、断网、PTY 和清理。Codex ChatGPT 文本 smoke 通过，dynamic-tool 往返验证一次；没有 CTF 题目容器运行或解题结果。


## 2026-10-07：阶段 D 首轮基础实现

六类 pack/playbook、脚本/产物、v2 evaluation、命令 replay 与受审 memory 的基础合成闭环已验收：真实 Docker development 6/6 verified、25 个回放检查点 matched、Reverse/Pwn PTY 通过、随机 holdout 6/6 verified，记忆版本引用/撤销通过。C4 首个受控 TCP 的 28 项必需检查及临时受审 CLI/TUI 通过；默认远端 profile 未安装。最新全量测试 100 项通过（13.05s）。未调用真实模型或私有 pilot；D 最终验收仍待多机制样本、工具扩展及真实受控评估。详见 [阶段 D 记录](phase-d/README.md)与 [C4 实机记录](phase-c/remote-acceptance.md)。以上较早章节保留历史基线，当时的未实现/未运行描述不代表本次进度。
