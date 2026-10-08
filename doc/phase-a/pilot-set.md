# 阶段 A4：pilot 候选清单

状态：26 道候选固定到 CTFTiny commit 并生成文件哈希清单；10 道只用本地附件、不连接远端箱的 pilot 已正式准入为私有本地数据集，并生成隔离快照。CTFTiny 仍在项目仓库外，不加 submodule、不上传题目数据。cry-babycrypto 有一次性模型传输授权并已完成 smoke；其余题目未获授权。

候选来源：[NYU-LLM-CTF/CTFTiny](https://github.com/NYU-LLM-CTF/CTFTiny)。挑战 ID/path 取自上游挑战清单。源码位于 `/tmp/ctfbot-ctftiny-stage-a`，固定 commit 为 `f1c9531672c45b24b7fb5f3aa44a7ac33d3602f8`。文件级 SHA-256 只读清单见 [pilot manifest](pilot-manifest.json)；challenge.json 只记录哈希和是否存在 flag 字段，不记录 flag 值。

## 选择原则与当前状态

- 首轮暂定 10 道、覆盖 crypto、forensics、reverse、misc 四类。评测 prompt 明确限定为本地附件；凡上游声明外部题目箱的条目，不连接远端目标。
- 10 道已从候选 shortlist 转为正式私有本地准入。附件答案泄漏初筛与用户要求的基本复核已完成；已按 allowlist 生成 agent workspace。除 cry-babycrypto 一次性获准且已消耗的单题 smoke 外，准入不包含模型数据传输；也不代表通过 Docker 宿主安全认证。
- 对全部 31 个 manifest 附件完成了本地 Docker 答案泄漏扫描和 14 次 Python/ELF 无参数 smoke；结果与三个需排除/分类信号见[准入审查](admission-review.md)。19 道附件未发现明显信号，3 道有信号，4 道没有本地附件。
- manifest 还包含另外 16 道候选供后续筛选：10 道声明 Compose 环境、4 道其他声明外部题目箱的条目，以及 2 道需要额外答案泄露审查的静态候选。它们不进入本轮 offline shortlist。
- 上游仓库根目录标注 GPL-2.0，但单题附件可能来自不同赛事或作者，未单独证实各项资产许可。用户授权仅作私有本地评估依据；不把题目、flag、writeup 或镜像随 ctfbot 分发或公开发布。
- 用户接受上游 gold/oracle 暂按已核实处理，遇到真实评测失败再详细判定；该约定记录为临时接受，不代表独立证明 gold 的语义。
- challenge.json、README、solver、hints、writeup、solution 与 flag 文件默认私有。只允许从 manifest 显式标为 `challenge_input_candidate` 且 hash 匹配的文件进入 workspace。

## 首轮 offline pilot（10 道，已正式准入为私有本地数据）

| 类别 | 候选 ID | 上游环境标记 | 计划提供给 agent 的附件 | 额外处理 |
|---|---|---|---|---|
| Crypto | `cry-babycrypto` | 静态文件 | `ciphertext.txt` | README 含答案标记，必须排除；已做单题导入隔离 smoke。 |
| Crypto | `cry-ecxor` | 静态文件 | `ciphertext`、`ecxor_handout_100.py`、`rfc8032.py` | 两个通用 flag 样式字符串未匹配 gold 或常见变换；按用户设定的基本确认门槛准入并保留复核备注。 |
| Crypto | `cry-perfect-secrecy` | 上游声明 box | `image1.png`、`image2.png` | 仅附件模式；不得连接 box。 |
| Forensics | `for-1black0white` | 静态文件 | `qr_code.txt` | README/solver 私有。 |
| Reverse | `rev-baby-mult` | 上游声明 box | `program.txt` | 仅附件模式；不得连接 box。 |
| Reverse | `rev-rap` | 上游声明 box | `rap` | 仅附件模式；二进制只在获准隔离运行时中执行。 |
| Reverse | `rev-tablez` | 静态文件 | `tablez` | build/solve/flag 文件私有。 |
| Reverse | `rev-ezbreezy` | 上游声明 box | `app` | 仅附件模式；不得连接 box。 |
| Misc | `msc-ezmaze` | 上游声明 box | `attachment/maze.pt` | 仅附件模式；不在宿主加载或反序列化该文件，不连接 box。 |
| Misc | `msc-quantum-leap` | 上游声明 box | `output` | 仅附件模式；不得连接 box。 |

上游环境标记只说明来源仓库声明了何种运行方式，不代表 ctfbot 已运行该环境或确认只靠附件即可解题。附件模式下解不出时应记录为 `unsolved` 或 `environment_error`，不得改为访问远端目标。

## 候选库存盘点

| 候选组 | 数量 | 当前处理 |
|---|---:|---|
| 无 Compose、未声明外部 box 的静态候选 | 6 | 4 道进入 shortlist；`for-whyos` 的 `console.log` 输入含精确 oracle 值，`rev-whataxor` 声明 README 为输入，均暂缓。 |
| 声明外部 box、但存在已声明文件附件的候选 | 10 | 选 6 道做 offline shortlist；其余 `cry-collision-course`、`rev-checker`、`rev-dockreleakage`、`msc-weak-password` 不连接、不导入为 pilot。 |
| 声明 Compose 的候选 | 10 | 只做了 YAML 静态审计；10 个上游配置都不应原样运行。 |
| **合计** | **26** | 10 道 shortlist，16 道暂缓；manifest 对所有记录给出源文件名、大小、SHA-256、输入角色和运行标记。 |

所有 26 份 challenge metadata 都存在 `flag` 字段；这只说明上游提供了候选 gold 值，不证明其格式、语义或 verifier 已独立校验。

## 导入与 Compose 审查

- 导入脚本校验源 commit、metadata 哈希、逐个输入 hash、路径、符号链接和大小；只复制 allowlist 附件与生成的通用 TASK，oracle 放到独立 controller-only 私有目录。默认只接收静态候选；6 个 offline artifact 候选需要显式 `--offline-artifact-only`，不提供远端访问工具。输入单文件上限 32 MiB、总量上限 64 MiB，输出目录必须位于项目和源码 checkout 外。
- manifest 初筛会检查 declared input 是否直接包含 gold oracle 的原始字节。后续隔离审查额外覆盖常见编码/变换形式、`strings`、归档内容和成员名、PNG 文本元数据，并把 Python/ELF 附件以无参数方式在隔离容器中执行。结果只保存脱敏元数据；检查范围和命中项见[准入审查](admission-review.md)。
- 对 shortlist 的 13 个输入做了不执行/不解析的字节头与 UTF-8 可解码检查：共 3 个 ELF、2 个 PNG、8 个 UTF-8 文本；附件总量 83,899 bytes。`maze.pt` 虽可按 UTF-8 解码，结构仍未解析，保持不加载/不反序列化。
- 10 道 pilot 的输入/oracle 分离、准确答案通过、错误答案拒绝检查均通过；此前每题重复导入两次后，附件、provenance、TASK 和 oracle hash 10/10 一致。当前正式快照位于 /tmp/ctfbot-stage-a-admitted-pilot/；oracle 为独立 controller-only 文件。一次 cry-babycrypto smoke 使用额外的一次性快照和授权；详见[解题 smoke 报告](solve-smoke-cry-babycrypto.md)。
- 10 个 Compose 配置全部使用共享外部 `ctfnet`、非 internal 网络，并至少包含一个可变镜像引用；6 个挂载 Docker socket，6 个发布未限制到 loopback 的宿主端口，`msc-showdown` 额外启用 privileged。详见 [Compose 审查](compose-review.md)。未拉取镜像、未启动服务。
- 附件审查没有启动远端题目箱或 Compose 环境。容器断网、输入只读、每题仅挂载声明附件；不安装 `.deb`，不反序列化 `maze.pt`。Python/ELF smoke 前后重建容器；这次执行不构成 Docker 宿主隔离安全认证。

## 正式准入门槛

每题只有满足以下条件才可从 `not_admitted` 改为内部私有 pilot 的 `admitted`：

1. 固定上游 commit/tree、逐文件 SHA-256、原始获取位置和日期；使用本轮答案扫描结果排除泄漏或含糊的附件。
2. 记录数据集、赛事/作者条款，以及二进制、镜像、第三方代码许可；用户只授权私有本地使用，许可未核实时不得打包或对外分发。
3. 使用 controller-side verifier；gold/oracle 按用户决定暂时接受，记录该约定并在真实评测失败时重开，不将 oracle 放入 agent prompt/workspace。
4. 静态文件题确认附件大小、类型和解析风险；不可信二进制、脚本和复杂附件只可在批准的隔离 runtime 中检查，不在宿主执行或反序列化。
5. 需服务环境的题目另行完成 Dockerfile、入口脚本、镜像 digest、网络与权限审查；禁止 daemon socket、privileged、host mount/network/device 和共享网络。
6. 获准 runtime 中验证启动、健康检查、停止、清理和重复性；agent 网络只能访问本题 per-run 服务，不能出网。

## 数据污染与许可策略

- NYU CTF Bench development split 用于构建/调试，test split 用作主评测；本 shortlist 仅作公开开发诊断，不报告为未见题泛化指标。
- 公开历史题可能已进入模型训练语料或公开 writeup；运行结果标注 `public/exposure-unknown`。后续需自创或获授权的隐藏题验证集。
- 不把 GPL 仓库快照、题目二进制、官方答案、writeup 或镜像 vendor 进项目。未来如需打包题目，先逐项审查许可；优先提供用户本地下载/导入并记录 provenance。
