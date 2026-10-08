# 阶段 A：环境与基线施工记录

日期：2026-09-30  
状态：最小闭环完成；扩展验收仍有后续项。  
对应计划：[详细设计与实施计划 §5](../AI_CTF_AGENT_DETAILED_PLAN.md#5-阶段-a环境与基线)

本目录记录阶段 A 的实际检查、候选方案和未解决项。执行性验收仅在对应检查真实执行后标为完成；对于明确采用静态评估的工作包，源码审查和决策记录就是其验收方式，不代表运行验证。

## 工作包状态

| 计划项 | 状态 | 本轮结果 |
|---|---|---|
| A1 范围确认 | 完成（文档） | 已定义本地文件、本地 Compose、远端 allowlist 的授权和拒绝边界，见[范围与威胁边界](scope.md)。 |
| A2 底座 spike | 完成（按静态参考范围） | 已审查 EnIGMA v0.7.0 的 CTF ACI/IAT、会话工具、摘要器、领域配置、依赖/API 风险及与 ctfbot 数据/安全契约的差异。为确认实际运行阻塞曾做隔离启动尝试，分别遇到 `KEY_INSTANCE_ID` 和 `MAP_REPO_VERSION_TO_SPECS` 导入错误；按用户决定，不再追查其兼容依赖、不运行题目、不做性能对照。记录见[runner 评估](runner-runtime-spike.md)。 |
| A3 sandbox spike | adapter 的 synthetic live smoke 通过；完整隔离验收未完成 | 经批准用本机静态 BusyBox 构建 `FROM scratch` 临时镜像，验证 ctfbot adapter 的基本配置、命令执行、超时 kill 与容器清理。之后经用户授权在同一类断网 profile 中做了题目附件审查；这不等于恶意镜像、mount/DNS 越界或宿主隔离安全认证，见[runner 与 runtime 评估](runner-runtime-spike.md)。 |
| A4 数据集准备 | 完成（10 道私有本地 pilot 已准入） | CTFTiny 固定在项目外；10 道只用本地附件的题目已按 hash 和 allowlist 生成实际快照 /tmp/ctfbot-stage-a-admitted-pilot/。正式 manifest 对所有题仍为 `model_data_authorized=false`；另用独立临时授权快照完成一次 cry-babycrypto 解题 smoke。见[准入审查](admission-review.md)和[pilot 候选集](pilot-set.md)。 |
| A5 基线协议 | 工程预算已固定；真实 provider usage 仍不可用 | 30 turns、60 工具调用、30 分钟 run、120 秒单命令和输出上限由 runner 强制；工具输出超限时保留摘要和 artifact 引用。单题 smoke 用了 1 turn、6 次工具调用并通过 controller verifier；usage 未返回，见[基线协议](baseline-protocol.md)。 |
| A6 基线执行 | headless loop 与 synthetic baseline 完成；单题真实 smoke 通过，批量 baseline 未运行 | provider-neutral loop、evidence、工具 registry、controller verifier、Codex adapter、`ctfbot solve` 和离线 `ctfbot baseline-smoke` 已实现。一次性获准的 cry-babycrypto smoke 得到 `verified`；它不等于批量 baseline。其他题目仍未获准传输。 |
| A7 选型决策 | ADR-001 已定；Codex App Server 暂作 ChatGPT adapter | 采用 ctfbot 自有薄 runner，不 fork EnIGMA；模型层通过 provider-neutral `ModelSession` 解耦，Codex App Server 是临时 adapter。后续新增 provider 实现同一契约并单独做真实兼容测试；不把当前 experimental dynamic-tool API 宣称为稳定。 |
| A8 TUI 可行性 | spike 验收完成 | Textual fake-event 原型验证了 resize、无颜色、长日志滚动、焦点、输入和安全文本显示；正式工作台与真实 session 集成留待后续，见[TUI spike](tui-spike.md)。 |

## 当前环境快照

- 项目工作树：`main`，基线提交 `00ac24b`，本阶段修改尚未提交。
- 系统 Python：3.14.4；项目声明 Python 3.11+。
- Docker 客户端/daemon：29.8.1。普通 workspace sandbox 访问 daemon socket 返回 `permission denied`；经审批在沙箱外只读运行 `docker info` 后确认 daemon 可用。daemon 为 Ubuntu 26.04.1 / x86_64、kernel 7.0.0-34、cgroup v2/systemd、overlayfs、runc、AppArmor/seccomp/cgroupns，4 CPU、约 6.98 GiB 内存。容器操作需单独审批，未将 Docker socket 暴露给容器。
- A3 合成 smoke：早期本地 `scratch`/静态 BusyBox 试验以 64 MiB/0.25 CPU/PID16 验证资源限额、断网、PTY 和强制停止清理。随后运行[`verify_ctfbot_adapter.py`](spikes/docker_runtime/verify_ctfbot_adapter.py)，由本机 BusyBox 构建离线 `FROM scratch` 临时镜像；验证 ctfbot DockerRuntime 命令执行、challenge 与 rootfs 写入拒绝、`/work` 写入/复读、非 root UID、断网配置、CPU/memory/PID/capabilities 约束、超时后容器 kill 和删除。临时镜像已清理。后续 CTFTiny 附件审查使用本机已有、以不可变 image ID 固定的 `strix-sandbox` image，不调用 Strix 或任何模型功能。Docker 操作经批准在本机 daemon 上完成；普通 workspace 仍不能直接访问 daemon。
- 当前环境未发现 `OPENAI_API_KEY`、`ANTHROPIC_API_KEY`、`GOOGLE_API_KEY`、`OPENROUTER_API_KEY`、`CTFBOT_MODEL`。
- 本机 Codex CLI 0.158.0 通过 ChatGPT 登录；配置模型 gpt-6-luna、effort max。文本连接 smoke、固定结果 dynamicTools smoke 和一次获准的 cry-babycrypto 解题 smoke 已运行。解题 smoke 为 1 turn、6 次工具调用并被 exact-string controller verifier 接受；provider 没有返回 usage。运行中模型读取了 workspace provenance；当前代码已改为只把 input 子目录提供给模型工具和容器，并由离线 synthetic 单测验证通过；没有启动 Docker 或调用模型。单次题目传输授权已消耗，不自动发起新请求。
- 阶段 A spike 将 Textual `8.2.8` 隔离安装在 `/tmp/ctfbot-phase-a-tui`；阶段 B 正式 TUI 已将 `textual==8.2.8` 固定加入 `pyproject.toml`。
- 本轮经用户授权将 31 个声明附件分题复制到 `/tmp` 私有临时目录，并在断网 Docker 中检查；做了 14 次 Python/ELF 无参数 smoke。临时副本自动清理，脱敏审查报告在 `/tmp`；项目内只保存 hash、路径与审查状态。没有启动挑战服务或访问上游 endpoint，也没有将题目附件提交到项目。

## 阶段 A 最小闭环

**通过。** 单题真实解题 smoke 已由 exact verifier 接受；此前暴露的 provenance 可见范围已收窄为仅向工具和容器提供 `input/`，并由离线 synthetic 单测验证实际 baseline wiring、文件工具边界和 Docker 只读挂载参数。该验证未启动 Docker、未调用模型。

## 扩展验收（不阻塞最小闭环）

以下工作保留为后续完善，不影响按“先完成后完善”进入下一阶段：

1. Docker adapter 的 synthetic live smoke 已通过；仍需补充异常路径与宿主隔离负面验证，且不得把容器结果描述为 VM 级安全认证。
2. cry-babycrypto 的一次真实解题 smoke 已获授权并通过；一次性授权已消耗。其余题目和批量 baseline 未获传输授权，开始前需重新确认题目、输入范围和模型预算。
3. Codex adapter 的真实 usage、错误、超时、取消及 experimental API 兼容性仍未验收；新增 provider 的真实测试可留到选型时，接入边界通过 `ModelSession` 保持解耦。

阶段 A 的纯合成 sandbox smoke、TUI 可行性 spike、10 道离线候选工程 smoke、10 个 Compose 静态审查、26 候选附件审查和 10 道私有 pilot 正式准入已完成；新 Docker adapter 的基本 synthetic live smoke 通过，headless baseline loop 与 batch scheduler 由 fake/synthetic 路径验证。EnIGMA 已按静态参考范围完成，不再是阻塞项。剩余关键项是 Docker 更广的异常/宿主隔离边界验收、Codex usage/错误/取消语义，以及按新授权开展的批量 baseline。单题真实 smoke 记录见[解题 smoke 报告](solve-smoke-cry-babycrypto.md)。

## Stage B 起点

阶段 B 的最小 Textual 单题工作区现已实现：它接受本阶段 importer 生成的 workspace，不接收服务题或远端目标；授权、路径/hash 和 digest 固定 image 在创建模型/runtime 前检查。TUI 与 headless `solve` 共用 application service，停止、事件查看和基础报告由合成 fake/synthetic 测试覆盖。本轮没有对真实 provider、Docker daemon 或真实附件执行 TUI 运行；相关 live 验收仍按题目授权和所启用 runtime profile 单独记录，见[详细计划 §6](../AI_CTF_AGENT_DETAILED_PLAN.md#6-阶段-b第一条可用产品闭环)。
