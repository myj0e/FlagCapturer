# 阶段 D：首轮基础实现

2026-10-07：**六类基础合成闭环验收通过，阶段 D 最终验收仍有剩余项。** 六类 development 题在真实 Docker 中 verified、报告/审计通过、25 个命令检查点回放 matched；Reverse/Pwn 真实 PTY 通过；独立随机 holdout 6/6 verified。显式 generic memory 的读取/引用、撤销与快照保留通过。C4 首个受控 TCP 的 28 项必需用例及受审 CLI/TUI 通过。最新全量测试 **100 passed in 13.05s**。未调用真实模型、未运行私有题目、未安装项目默认远端 profile。详见 [D 合成实机验收](docker-acceptance.md)及 [C4 实机验收](../phase-c/remote-acceptance.md)。

上下文管理增量计划：见 [Agent 上下文管理改进计划](agent-context-management-plan.md)，CM01–CM06 已实施，详见[上下文实施记录](agent-context-management-implementation.md)。确定性测试与真实 Docker 验收通过，真实模型效果对照未执行。

## 六类覆盖矩阵

2026-10-08 工具镜像增量：general-v2 已补齐常用 Python 解题库、系统命令、GDB/binutils、
32/64 位编译运行环境及取证/归档工具，真实 offline runtime 下 11 组功能和边界检查通过。
同一固定镜像复跑六类 development 6/6 verified、命令回放 matched、holdout 6/6 verified。
TUI 默认解析 `ctfbot-tools:candidate`，旧固定服务 profile 保留原镜像；详见
[工具镜像与验收](tool-image.md)。这项验收包含 GDB 调试自身子进程、pwntools ELF/cyclic、
PCAP 解析与 Steghide 自建往返，不能扩展为广泛 Pwn、逆向或真实模型解题能力已验收。

每个类别分别提供 JSON-compatible `pack.yaml`、playbook、依赖列表、来源/许可证记录与代表性合成题定义。安装包包含这些资源，避免依赖仓库当前目录读取文档。下表六类 authored-v1 代表机制均已通过 Docker 解题与命令回放；最后一列列出尚未覆盖的扩展能力。

| 类别 | 当前工作流 | authored-v1 代表机制 | 当前限制与验收状态 |
| --- | --- | --- | --- |
| Crypto | hex/base64 解码；最多 100000 次试除的 RSA 小因子假设；保存并重跑 Python 脚本 | 独立随机实例的 base64 编码 | base64 闭环通过；RSA、多种密码机制、Sage/椭圆曲线及真实模型结果未验收 |
| Digital Forensics | 文件 magic/hash/有限 strings；ZIP 目录；容器内脚本提取与声明来源 lineage | 小型 ZIP 内相关条目 | 不自动解包；复杂归档、pcap、音频和 exif 依赖可用镜像，这些扩展未验收 |
| Stego | PNG chunk/CRC/text；单一 RGB LSB 长度头假设 | P6 PPM 的 RGB LSB | 不代表任意图片/音频隐写覆盖；PNG/其他机制未验收 |
| Reverse | ELF32/64 triage、GNU stack、strings；可用时 objdump；脚本记录 | 自编译 ELF 的 XOR 与输入 gate | PE/packed/decompiler 未实现；objdump/GDB 为可选依赖，其调试工作流未验收 |
| Pwn | ELF 保护线索；已有 session 与保存脚本组合的本地进程实验/重跑 | 自编译 ELF 的结构化输入认证字段覆盖 | 教学 I/O/认证 fixture，不是广泛内存破坏 exploit 覆盖；pwntools/GDB 工作流未验收 |
| Web | 离线 HTTP 响应；固定授权 TCP endpoint 上的 GET/HEAD/POST；原始请求/响应 hash | 离线 HTTP 注释中的编码 | 远端受 C4 profile gate 限制；无 TLS/browser/cookie session/扫描器；当前只验收离线响应及受控 plaintext HTTP |

混合/未知类别：`workflow_discover` 按文件扩展名及少量 magic 给出多标签、线索和置信度；未知或跨类线索保留 core command/session 回退。每次 discover 会记录新的 routing event；模型可以切换读取不同 playbook。分类不授予网络权限，也不改变 oracle 或验证状态。该能力不算第七类覆盖，也没有分类准确率结论。

## 使用入口

```sh
ctfbot packs
ctfbot packs crypto

# 仅创建题目、外置 oracle 和私有 dataset.json，不运行题目或模型：
ctfbot fixtures --output /private/ctfbot-development --split development
ctfbot fixtures --output /private/ctfbot-holdout --split holdout
ctfbot audit-split --development /private/ctfbot-development/dataset.json --holdout /private/ctfbot-holdout/dataset.json
```

生成器默认 `model_data_authorized=false`。仅对明确准备发送给模型的自建题目，在创建时添加 `--authorize-model-data`。该参数不作用于现有私有 pilot；不应手工改动只读快照绕过 hash 记录。Reverse/Pwn 的 authored fixture 编译需要本机 `cc`/`gcc`，只编译项目自编写的源码，不在宿主执行产物，不自动安装编译器。生成失败可能留下部分目录，需检查后另选新输出目录。

### Agent 可发现工具

| 工具 | 契约 |
| --- | --- |
| `workflow_discover` | 元数据与多标签 hints；最多检查 64 个已准入输入的小型前缀 |
| `workflow_read` | 指定类别的操作手册、版本、依赖与已知盲点 |
| `workflow_environment` | 容器内检查 Python、所需/可选二进制与模块；不安装依赖 |
| `workflow_run` | 指定 pack/operation/input；固定 argv、共享超时与输出限制；保存底层 command artifact |
| `script_save` | 至多 8 KiB Python 源码，写入全新 `/work/*.py` 并保存内容 hash；修改用新文件名 |
| `script_run` | 校验已保存源码 hash 后执行读到的相同字节；共享容器与预算 |
| `artifact_export` | 至多 256 KiB `/work` 文件，保存私有 artifact 与声明的 challenge 输入父节点/hash |
| `http_request` | 仅受控 remote runtime 注册；固定 endpoint、受限方法/path/body，不跟随重定向，不读取代理环境变量 |
| `memory_search/read` | 仅显式选定 namespace 时注册；读取本次 run 固定的已审核版本 |

产物 lineage 是操作者/模型声明的父节点，不是自动证明的数据流。所有复杂解析和 ELF 执行都在原有 sandbox 内，controller 不解析 ELF/ZIP/图片。原始输出留在私有 evidence，报告展示状态、hash 和引用。flag 只有 `candidate_submit` 经 controller verifier 才能成为 verified。

## 受控评测

版本 2 数据集固定 dataset ID/version/split/license、case ID、附件快照 provenance hash、主类别/多标签、exposure、contamination 和 mechanism。与阶段 A 的固定 10–20 题 batch 契约分开，支持 1–100 个 case 和 1–3 次重复。

`evaluate` 经 TUI/solve 共用的 application service 预检所有 case；逐题模型传输授权、service/remote gate、memory 策略均不可省略。需要显式总 turns、tool-call 和 wall-time 预算，以及 `--confirm-model-usage`：

```sh
# 会使用模型额度；当前文档记录未执行这条命令：
ctfbot evaluate --dataset /private/ctfbot-development/dataset.json \
  --runtime-image sha256:<已准备的不可变镜像ID> --output /private/evaluations \
  --max-total-turns 12 --max-total-tool-calls 120 --max-total-wall-time 600 \
  --repetitions 1 --confirm-model-usage
```

holdout 必须显式传 `--development-reference`，执行 byte/hash/ID separation audit，并使用 blind memory policy。新生成实例的机制仍公开；分离只证明实例字节与 ID 不重复，不证明机制独立、模型训练未覆盖或真实泛化能力。真实隐藏/改编数据集仍需来源、许可、样本和额度授权。

每轮写入私有 `summary.json`/`summary.md`，分别提供六类与逐题/重复结果、verified/candidate-only/unsolved/environment/provider/not-run 计数与分母、时长 mean/range/p50/p95、usage、重复动作和 policy denial。费用暂为 unknown，不把缺失 usage 当成零费用。条件与每次 run 的 provider、pack、memory 版本保留在结果中；变更中的配置不能直接用于固定条件效果比较。

预算先预留每轮上限，确定使用量后回收未用 turns/tools；控制器无法确定用量时保留整个 allocation。预算耗尽或用户停止后，其余 case 标记 not-run，不自动重试。工具调用尝试数与真正允许执行的工具数可能不同，AgentLoop 对超额请求仍记录拒绝。

## 报告、审计和回放

```sh
ctfbot audit-run --run-dir /private/runs/<run-id>
ctfbot replay --run-dir /private/runs/<run-id> --workspace /private/case/workspace \
  --oracle /private/case/oracle.json --output /private/replays --wall-time 300
```

`audit-run` 校验 artifact hash/bytes 和事件次序；不会执行命令或调用模型。evidence 没有数字签名，该检查不是来源真实性证明。

`replay` 是显式的 Docker 操作，仅接受完全匹配的原始 offline 快照和固定镜像。它在新的无网络 solver 中，按顺序执行已记录 argv，比较退出码、超时、截断及 stdout/stderr hash。replay 使用持久 Docker 请求与所有权恢复，状态目录在输出根的 `replay-state`；异常中断后运行 `ctfbot replay-recover --output /private/replays` 处理该目录，未确认请求仍保持阻断。

这不是模型推理回放，不自动恢复交互 session，不访问远端，不调用 memory，不提交 flag。结果注明跳过的 session/network 部分，并输出 matched/diverged/timeout/error 与分子/分母。命令输出一致不能代替重新验证 oracle。旧 run 没有 `command_execution` 记录时可审计，不承诺可执行回放。

## 受审记忆

四个 namespace：`manuals`、`failures`、`challenge`、`writeups`。默认不读任何记忆；blind 只允许经审核的 generic manuals/failures；challenge 条目还须匹配当前题目；challenge/writeups 仅 practice 模式允许。此处是小型、确定性的全文查询，不引入 embedding/RAG 外部服务。

```sh
ctfbot --memory-root /private/memory memory propose --source-file /private/note.md \
  --namespace manuals --source-url 'repo:reviewed-note' --license MIT --tag crypto
ctfbot --memory-root /private/memory memory review --id <candidate-id> \
  --reviewer <审阅人> --basis <对照源文件与证据的依据> --decision approve --contamination generic
ctfbot --memory-root /private/memory --memory-namespace manuals solve <其他必需参数>
ctfbot --memory-root /private/memory memory revoke --id <approved-version-id> --basis <失效原因>
```

候选、批准、拒绝和撤销分别保存。批准前复核原始 source hash、许可和污染级别，已知 flag 模式不能进入 generic namespace；启发式检查无法识别所有答案编码或题目常量，reviewer 必须人工审查。每次批准产生内容寻址的新版本；本次 run 固定可读版本，撤销影响后续 run，已记录快照保留供审计。没有自动写入、自动批准或自动将题解加入盲测。

## 剩余交付与发布门槛

1. 已取得测试和合成验收授权；100 项自动化测试通过，后续变更继续重跑回归。
2. C4 首个受控 TCP 的隔离、停止/到期、回执/崩溃恢复与临时受审 CLI/TUI 验收完成；真实远端目标仍须独立 grant、有效时段及匹配审核，不复用已关闭的测试端点。
3. 六类确定性 provider + 真实 sandbox 的 oracle/evidence/report/命令回放完成，Reverse/Pwn PTY 通过；Web plaintext HTTP 在 C4 自建端点验收。TLS/browser 等按最终目标另行扩展。
4. 补足每类更多机制、工具 image/source/license inventory；缺失依赖不计作模型解题失败。
5. 获得明确题目传输和模型预算授权后做有限真实模型评估；provider usage/计费语义未验收前不发布费用或泛化收益结论。
6. 暂不加入多 agent、planner、race、browser、外部 RAG 或扫描器；没有固定预算对照收益证据，不增加这些复杂性。当前薄 wrapper 的 token/成本收益也未测量，不声称性能提升。

多机制样本、真实受控评估与最终能力范围补足前，阶段 D 总状态保持“基础合成闭环已验收，最终验收未完成”。覆盖矩阵中的“未验收”限制指相应扩展机制；authored-v1 六类样例结果以上述实机记录为准。

## Agent 通用可靠性增量（2026-10-08）

G01–G06 首版契约已实现并完成确定性回归、真实 Docker 与 Textual 合成验收。候选与结束分离、来源绑定、局部检查、错误分类、实际 prompt 记录和 `diagnose-run` 只读诊断均可使用。详见[实施记录](agent-common-improvement-implementation.md)。真实模型前后效果对照尚未执行；本增量不改变阶段 D 最终验收未完成的结论，也不代表 Reverse 专项能力全部交付。
