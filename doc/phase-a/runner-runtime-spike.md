# 阶段 A2/A3/A7：runner 与 sandbox spike

状态：EnIGMA/SWE-agent v0.7.0 的源码静态评估完成；一次隔离启动尝试已记录依赖 API 冲突，但按项目决定不再修复或运行该候选。fake/direct-tool loop、两轮合成 Docker runtime（含 ctfbot adapter live smoke）、TUI spike 和单次历史真实 dynamic-tool 往返已完成；ctfbot Stage A headless loop/provider adapter/Docker adapter/batch runner 已实现并经过离线 fake 测试。准入题运行和真实解题基线未完成。

## 本地观察

- Python 3.14.4 可运行当前无第三方依赖的骨架；项目运行下限为 3.11。计划固定一条 CI / benchmark Python 版本（建议 3.11 或 3.12），开发机可以继续使用更新版本。
- Docker CLI/daemon 均为 29.8.1。普通 workspace sandbox 内访问 `/var/run/docker.sock` 被拒绝；经批准在沙箱外只读查看 daemon 后确认 Docker 服务可用。环境为 Ubuntu 26.04.1、kernel 7.0.0-34、x86_64、cgroup v2/systemd、overlayfs、runc、AppArmor/seccomp/cgroupns、4 CPU 和约 6.98 GiB 内存。未确认 rootless 状态。
- Docker 容器操作在此工作区需要单独审批；未改 socket 权限/所属组，也未向容器挂载 Docker socket。现有 `strix-sandbox` 5.9GB 镜像没有运行。

## 候选对比

| 候选 | 值得借鉴 | 对 ctfbot 的缺口/代价 | 当前判断 |
|---|---|---|---|
| EnIGMA / SWE-agent v0.7.0（静态参考） | 有面向 CTF 的 prompt/config；按 crypto/forensics/pwn/rev/web/misc 区分配置；提供交互工具、摘要器和成熟 command set。适合参考会话操作、长输出处理和领域工作流。 | 默认 CTF 模板采用 `DISCUSSION` + 单个文本 command 协议，环境允许联网/安装软件；需改造才能满足 ctfbot 的事件/evidence/verifier/授权契约。依赖声明不一致：`swebench 5.0.2` 路径缺少 `KEY_INSTANCE_ID`；`1.1.0` 路径缺少 `MAP_REPO_VERSION_TO_SPECS`。 | **仅作源码静态参考**；不 fork、不纳入阶段 A 实跑或性能对照。 |
| ctfbot 自有薄 runner | 可直接采用原生 provider tool calls；让 orchestrator 拥有 budgets、policy、evidence、verifier 和统一事件协议；runtime/model adapter 可替换；便于 TUI/headless 共用。 | 需要自行实现调用重试、上下文整理、结构校验、stop policy、交互 session、日志与回放；早期 bug 和评测投入由本项目承担。 | **阶段 A 采用**；先只做一种 provider、少量通用工具和一个可执行 backend。 |
| SWE-ReX 执行层 | 为 shell runtime 提供 local/Docker/remote 等部署抽象，支持持久命令 session 和多个并发 session，减少自己处理 PTY/命令完成检测的工作。 | 是执行接口，不是 agent loop/evidence/verifier；network allowlist、mount、安全 profile 和预算 enforcement 仍由 ctfbot/backend 配置负责；尚未安装/运行，未测启动/清理/会话可靠性。 | 作为 sandbox/session adapter 候选；未验证前不加入生产依赖。 |

SWE-agent v0.7.0 的上游 [issue #1319](https://github.com/SWE-agent/SWE-agent/issues/1319) 也记录了旧版本在更新后的 SWE-bench/Modal 依赖下启动失败；本机复现到的是 `KEY_INSTANCE_ID` 导入失败。两者共同表明旧 tag 不能依赖浮动依赖做可复现基线，issue 本身不替代本地复现。

## EnIGMA 启动尝试记录（2026-09-30，已封存）

- 原隔离 venv 由未锁定依赖解析到 `swebench 5.0.2`，`run.py --help` 因 `KEY_INSTANCE_ID` 缺失失败。
- SWE-agent v0.7.0 的 `sweagent/api/requirements.txt` 固定 `swebench==1.1.0`、`anthropic==0.25.3`、`tokenizers==0.15.2`。在只写 `/tmp` 的兼容 overlay 中安装这些相关版本后，`KEY_INSTANCE_ID` 阶段通过，但 `run.py --help` 接着因 `sweagent/environment/swe_env.py` 所需的 `MAP_REPO_VERSION_TO_SPECS` 不存在而失败。
- 这不是元数据 `pip check` 能发现的冲突：主 requirements 声明 `swebench>=2.0.0`，API profile 固定 1.1.0，代码路径又同时依赖 constants 中不同版本的导出。该启动失败只作为维护风险证据；按项目决定不继续找兼容版本、不改 EnIGMA 源码、不把该环境接入项目依赖，也不作为阶段 A 阻塞。

## 从 EnIGMA 配置看到的适配点

- 官方 release v0.7.0 将 CTF challenges、Interactive Agent Tools（如 GDB）及长输出 Summarizer 列为主要能力，说明持续会话和摘要值得纳入参照。
- `default_ctf.yaml` 把 Linux shell、预装安全工具、联网容器、可临时安装软件和 `submit` 命令写进上下文，并要求模型一次发一个 command；这适合其研究 benchmark，但与本项目“默认断网、明确 allowlist、provider 原生 tool schema、候选与 verified 分级”不兼容。
- 同一默认配置提示不要使用普通交互命令，并 blocklist vim/vi/emacs/nano/nohup/gdb；GDB/服务交互由其自定义命令适配。这提醒我们必须验证 `session` 工具的真实 PTY/输入输出语义，不能只比较 CLI 能否启动。
- 7 类配置文件覆盖 crypto、forensics、misc、pwn、rev、web，领域提示可参考，但 ctfbot 不应复制其 prompt 或 challenge 答案；只借鉴通用工作流并保留来源。

## ctfbot 阶段 A 实现（2026-09-30）

- 新增 provider-neutral `ModelSession`/tool turn 接口、headless `AgentLoop`、JSONL event 与 content-addressed evidence store、只读 challenge file tools、隔离 command tool 和 controller-only exact verifier。工具参数哈希与私有 artifact 分开保存；路径遍历和 symlink 拒绝，flag 只有经独立 verifier 后才会记录为 `verified`。
- 新增 Codex App Server dynamic-tool session adapter 和 `ctfbot solve`。真实模型调用须显式传入 `--confirm-model-usage`；本轮未调用。Adapter 仍需真实 usage、error、timeout、cancel 与 CLI/API 版本验证。
- 新增 Docker command session adapter：digest 固定镜像、`--network=none`、只读 `/challenge`、2 GiB tmpfs `/work`、只读 rootfs、宿主非 root UID、drop capabilities、no-new-privileges、CPU/memory/PID 与输出上限；每 run 停止并删除容器。之后在本机 Docker daemon 上用 synthetic fixture 运行 adapter smoke，基本命令、文件权限、运行用户、配置约束、超时 kill 和容器清理均通过；这不是恶意镜像/宿主逃逸安全认证。
- 新增 `ctfbot baseline` case-set runner，要求 10–20 道已正式准入候选、覆盖至少四类，并指定三道不同类别复跑；全量准入预检先于 model/runtime 启动，summary 只记录脱敏结果。当前 A4 准入为 0，故真实 batch 不可启动。

## Direct-tool loop 原型

在 [`spikes/direct_tool_loop`](spikes/direct_tool_loop/README.md) 增加标准库 fake-provider 原型。手动运行结果：

- fake provider 经过 5 个合成回合，产生 workspace list/read、一次 `../outside.txt` 越界尝试和 candidate submit；越界调用被拒绝，candidate submit 调用独立 controller-only synthetic oracle，最终状态为 `verified`。
- 事件逐条 append 到临时 `events.jsonl`；workspace.read_text 原文保存为 evidence artifact 并按 hash 引用；候选原文不进事件参数，只保留 hash。读路径、工具注册、输出上限均经本地策略检查。
- synthetic oracle 与 workspace 分目录，目录权限分别受限；fake model transcript 固定且只识别 synthetic fixture，不能视作 CTFTiny solve 或 agent 能力成绩。Fake usage 为 `null`。
- 这验证了“规范化模型 tool call → 本地注册/参数与路径校验 → evidence/event → 独立 verifier”的数据边界可用标准库表达；没有验证真实 agent 策略、并发/重试、provider usage、生产持久化、session 或 sandbox 隔离强度。

运行命令：`python3 doc/phase-a/spikes/direct_tool_loop/loop.py`。

## 本地容器隔离 smoke

在 `/tmp` 临时目录中以本机静态 `/usr/bin/busybox` 构建了 `FROM scratch` 镜像，构建过程没有下载基础镜像。使用项目内脚本 [`verify_runtime.py`](spikes/docker_runtime/verify_runtime.py) 在 synthetic fixture 上运行：`--network none`、`--read-only`、内存 64 MiB、CPU 0.25、PID 上限 16、当前 UID/GID、capabilities 全部丢弃及 `no-new-privileges`；input bind mount 为只读，workdir 为可写 bind mount。

结果：input 可读，input/rootfs 写入被拒绝，可写 workdir 文件能由宿主读回；cgroup 内存显示 `67108864`、CPU `25000 100000`、PID 上限 `16`，并发子进程压力读到 `pids.current=13`。64 MiB 限额下尝试写入 128 MiB 以 exit 137 失败。容器 `-i -t` 中 `read/echo` PTY 数据往返成功；另一个 sleep 容器经 `docker kill` 后以 `--rm` 清理且无残留。`--network none` 下访问 `1.1.1.1` 失败。临时镜像和容器已在脚本 `finally` /清理步骤移除。没有题目附件或 Strix image 被运行。该 smoke 是配置/生命周期验证，不是恶意镜像逃逸、DNS/redirect、宿主机强隔离或生产安全认证。复现见[Docker smoke recipe](spikes/docker_runtime/README.md)。

新增的 ctfbot adapter smoke [`verify_ctfbot_adapter.py`](spikes/docker_runtime/verify_ctfbot_adapter.py) 再用离线构建的静态 BusyBox `FROM scratch` 镜像验证真实 `DockerRuntime`：busybox 命令执行、`/challenge` 与只读 rootfs 写入拒绝、`/work` 跨 exec 持久、运行 UID 与宿主非 root UID 一致、Docker inspect 中 network/memory/CPU/PID/capability/no-new-privileges 配置、命令超时后容器被 kill，以及正常清理无残留。该脚本无挑战文件、无网络构建；执行成功。它验证适配器基本生命周期，不覆盖恶意镜像行为、特殊路径/mount 绕过、网络协议负面探测或 VM 级宿主隔离。

## 已完成的静态评估与 ctfbot 后续验收边界

EnIGMA 已按静态参考范围评估完毕。阶段 A 继续检查 ctfbot 自身的 runtime、provider 和题目基线，不运行 EnIGMA。未来如需做性能/兼容性对照，应另开任务并固定题目、模型预算、镜像和 verifier。

| ctfbot 验收项 | 已有证据 | 仍需完成 |
|---|---|---|
| fake/local provider tool call | Codex adapter fake protocol 与单次历史真实 dynamic-tool 往返 | usage、错误、超时、取消及 API 兼容评估；真实请求须重新授权 |
| 只读 input、可写 workdir、artifact 取回 | DockerRuntime synthetic live smoke 验证 input/rootfs 拒写及 `/work` 跨 exec 持久 | 恶意/异常路径、host mount、DNS/egress 边界的负面验收 |
| 持久 shell、GDB 或 nc 输入/输出 | 一次 synthetic PTY read/echo | ctfbot session 复用、长任务、interrupt、超时和协议错误 |
| 模型/tool/总 wall-time 预算 | Docker memory/PID limits 已观察到配置与受限行为；adapter 超时可 kill 整个容器 | provider deadline 与 run wall-time 联合边界、批量预算端到端验证 |
| event/evidence 和 verifier | ctfbot 采用原生 schema；fake/synthetic loop 可追溯到 artifact 与 oracle | 通过正式 pilot 检查真实轨迹和验证流程 |

## ADR-001：runner（阶段 A 决定）

阶段 A 采用 ctfbot 自有薄 runner；EnIGMA v0.7.0 仅作源码静态参考，不 fork、不运行。该 ADR 决定项目架构边界，不声称薄 runner 解题效果优于 EnIGMA。未来只有出现明确需求时才单独立项做同条件运行对照。

## 暂定 ADR-002：sandbox

尚不选 Docker CLI adapter 或 SWE-ReX 为默认实现。最低可接受 backend 应有：per-run workspace/mount policy、默认无网、端点 scope enforcement、cgroup 资源限制、并发/时限、会话/PTY API、终止与残留清理、镜像 digest、可审计事件。完成上述完整验证之前，不把容器安全能力写成已实现事实。

## 阻塞和恢复条件

- 普通 workspace sandbox 无法访问 Docker daemon socket；已经批准并完成 ctfbot Docker adapter 的 synthetic live smoke。运行 challenge 镜像仍需逐题准入、镜像审查与更完整隔离策略评估。
- EnIGMA/SWE-agent v0.7.0 的源码和依赖已静态检查；启动尝试记录了 API 冲突，不继续兼容修复。SWE-ReX 尚未安装/运行，属于未来可选 runtime 评估。Textual `8.2.8` 仅安装在临时 venv 做 TUI spike，没有加入生产依赖。
- Codex CLI `0.158.0` 已处于 ChatGPT 登录状态；一次文本 smoke 和一次常量工具往返已发生。该调用的 usage 未归档，用户只授权了这一次真实工具 smoke，后续不应自行再请求模型。
- CTFTiny 固定在 `/tmp` 并写入 hash-only manifest；10 道静态/offline artifact 题已正式准入为私有本地 pilot，并生成独立 workspace/oracle 快照。没有把题目上传到项目、发送给模型或连接远端 challenge box。
- Docker adapter 隔离负面验收、真实题目 smoke/baseline 和多次复跑仍未完成；EnIGMA clean start/对照按项目决定不属于阶段 A。ChatGPT/Codex 暂作临时 provider adapter，新增 provider 的真实兼容测试延后；fake loop 通过不代表 CTF 解题能力。
