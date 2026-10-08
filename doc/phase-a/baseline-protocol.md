# 阶段 A5：pilot baseline protocol

状态：首轮工程预算与结果 schema 已冻结；Codex App Server 是临时 ChatGPT adapter，FakeModelSession 已用于离线协议验证。10 道私有本地 pilot 已正式准入；一次性获准的 cry-babycrypto 真实解题 smoke 已通过 controller verifier。该结果是单题工程 smoke，不是批量 baseline；后续题目数据传输仍需单独取得用户确认。

## 比较目的

先测出单主 agent、工具驱动循环在公开 development 题上的可复现基线，并把题目环境问题与 agent 解题失败分开。阶段 A 不做 prompt tuning，不将同一公开 test 集反复用于调参。公开题结果只用于开发诊断，不宣称对新题的泛化能力。

## 每次运行必须冻结的元数据

| 维度 | 必记字段 |
|---|---|
| 代码 | ctfbot git commit、Python 版本、依赖 lock/hash、配置版本、prompt hash、tool schema/pack hash。 |
| 模型 | provider、精确 model ID/revision、API 版本、采样参数、最大输出 tokens、请求重试策略；key 只记是否配置，永不写入日志。 |
| 题目 | 数据源和 split、挑战 ID、challenge/附件 SHA-256、镜像 digest、授权/许可状态、环境启动/停止版本。 |
| 预算 | 最大 model turns、总 tokens、工具调用数、单工具 timeout、wall clock、CPU、内存、PID、workdir 上限、每次与累计输出字节上限、成本上限（若 provider 提供可靠计价）。 |
| 轨迹 | UTC 时间、事件序号、请求/响应摘要、工具名和经脱敏参数、exit code、耗时、usage、artifact/hash 引用、policy decision、stop reason。 |
| 结果 | 每个 flag candidate 的来源 event/artifact、验证器类型、验证状态、失败/环境异常分类、人工介入点。 |

## 首轮建议预算

非 token 工程预算由首版 runner 强制；token/cost cap、Codex usage 归一化和实际 provider 取消能力仍未实现或验证：

| 限制 | 首轮提案 |
|---|---:|
| 模型回合 | 30（runner 已强制） |
| 工具调用 | 60（runner 已强制） |
| 每个 run wall time | 30 分钟（runner 已强制） |
| 单次命令等待 | 120 秒（runner 已强制；超时杀掉整个 sandbox session） |
| 容器 CPU / memory / PID | 2 vCPU / 4 GiB / 256 |
| 可写 workdir | 2 GiB |
| 返回给模型的单次/累计工具输出 | 16 KiB / 256 KiB（runner 已强制；原文由私有 artifact 引用） |
| tokens / 金额 | 当前 Codex App Server adapter 未实现硬 token/cost cap；确认 provider 能返回 usage 并确定可执行上限前，不得声称该预算已完整强制。 |

同一挑战不同难度可能需要不同预算，但第一版比较必须用一致预算；确需例外时单独标记，不混入总体 solve rate。

## 单题真实解题 smoke（2026-09-30）

- 授权：用户明确批准一次向 ChatGPT/Codex 发送真实解题数据。授权仅覆盖 cry-babycrypto 的 manifest-listed ciphertext.txt、通用 TASK.md，以及单次运行最多 3 个模型回合、6 次工具调用；没有授权其他题目、未来复跑或批量数据传输。主 pilot manifest 保持所有挑战 model_data_authorized=false；运行使用 /tmp 下单独生成的一次性 manifest 和快照。
- 运行：Codex App Server，gpt-6-luna / max；固定本地 Docker image ID sha256:61066f13fb37f4849473646835763de8de96d750769caf0797e7aaa6220d7b60，运行时网络关闭、根文件系统只读。真实用量为 1 model turn、6 tool calls、240 秒总 wall-time 上限。
- 结果：verified，controller-side exact-string verifier 接受候选。共 6 个工具调用：列目录 2 次、读取文本 3 次、提交候选 1 次；无 command_run。完整运行证据保存在本机私有目录 /tmp/ctfbot-stage-a-chatgpt-runs/0cc84d05-0ce8-460a-baf0-b3f032426d06，目录权限 0700、事件文件权限 0600。不在项目文档复制候选答案或题目附件。
- Usage：App Server turn 未提供 usage 对象，因此 token 数与费用未知；不得把此次 smoke 描述为已完成成本计量。
- 数据边界发现：模型在本次运行中读取了 workspace 的 provenance.json，其中仅有题目 ID/category、来源与输入哈希、导入模式和本次授权文字；不含 oracle、flag、writeup、solver 或其他挑战数据。根因是当时的 runtime 和文件工具访问了完整 workspace。已将两者改为只挂载/读取 workspace/input 子目录；任务文本仍由 controller 放入初始 prompt。针对真实 baseline wiring 的离线 synthetic 单测已通过，验证工具路径和 Docker mount 参数只指向 input；没有启动 Docker 或调用模型。
- 结论：这证明当前 Codex App Server dynamic tools、工具 loop、隔离容器与 controller verifier 能在该单题样例端到端工作。它不证明通用解题能力、其他题目可解、provider 稳定性、token/cost 限制或 Docker 宿主隔离安全；后续传输和复跑都要重新授权。

## 结果等级与停止条件

- `candidate`：模型或确定性扫描发现可能的 flag；记录原始来源，不代表正确。
- `format_valid_candidate`：匹配题目 flag 格式；只是一种候选，不算成功。
- `verified`：独立于模型推理的 benchmark oracle 精确匹配，或 challenge validator/success signal 明确确认。保存验证输入、输出和验证器版本。
- `manual_confirmed`：人工确认并记录 reviewer 与依据；与自动 `verified` 分开统计。
- `unverified`：没有可用 oracle/validator；只可报告候选与证据，不能宣称解决。
- `rejected`：候选被 oracle 明确否定。

结束原因独立记录：verified、budget exhausted、no progress、user cancelled、policy denied、environment error、provider error、agent error。服务启动失败不得计为 solve failure。

### 批量 case-set 接口

headless 命令 `ctfbot baseline` 读取 schema version 1 的私有 JSON 文件：`cases` 为 10–20 个 `{challenge_id, workspace, oracle}` 路径项，`repeat_challenges` 为恰好 3 个不同类别的 ID。所有 workspace 必须在准入后重新导入，provenance 标记 `formal_admission: admitted`，并单题记录 `model_data_authorized: true` 与授权依据；runner 会在创建 provider session 或 Docker container 前预检整个 case-set。

命令需要明确的 `--confirm-model-usage` 和 batch `--max-total-model-turns`（硬上限 690）；summary 写入私有 run 目录，不包含 oracle 值。样例结构见[case-set 格式样例](case-set.example.json)。

## 执行与复跑

1. 从已批准的 pilot manifest 生成只读 run snapshot，记录哈希和工具/镜像版本。
2. 固定预算和 model config 后冷启动单 agent；默认无 writeup/RAG、无人工 hint。
3. 每题先跑一次；从不同类别选至少 3 题做第二次相同配置复跑，报告 run 间差异。
4. 失败轨迹由未参与运行的人抽查，按环境、模型/provider、工具选择、脚本/分析、验证器、预算分类。
5. Prompt/工具修改后新建实验 ID；不得覆写历史 run，也不能把调参后成绩称作 baseline。

## 当前空缺

- 临时 provider 是 Codex App Server / `gpt-6-luna` / `max`；单次历史 tool smoke 的 usage 未归档，尚无可靠 token/cost cap 或真实错误/取消策略。Agent loop 只依赖 `ModelSession`，后续新增 provider 可独立实现和验收 adapter。
- 普通 workspace 无 Docker socket 权限；经批准的本机 ctfbot DockerRuntime synthetic live smoke 已验证临时 BusyBox 镜像上的基本命令、只读挂载、可写临时区、运行 UID、资源配置、超时 kill 和清理，但没有 challenge image digest 或题目运行协议，也未认证恶意输入隔离。
- CTFTiny 26 道候选已生成文件级 SHA-256 和 oracle 私有标记；31 个声明附件已在断网 Docker 中完成一次答案泄漏扫描，14 个 Python/ELF 附件完成无参数 smoke。19 道无明显信号、3 道有排除/分类信号、4 道没有本地附件；用户暂时接受 gold/oracle，失败后再复核。10 道私有本地 pilot 已正式准入并生成 workspace allowlist 快照；A3 宿主隔离验收未完成，见[准入审查](admission-review.md)。
- headless agent loop、JSONL event/evidence store、exact-string controller verifier、单 run ctfbot solve 和 10–20 道 case-set 的重复批量 runner 已实现；synthetic fake-provider smoke 与一次真实单题解题 smoke 均完成，后者通过。批量 runner 尚未产生真实基线。
- 用户授权 CTFTiny 仅作仓库外私有本地 pilot，10 道已正式准入；主 manifest 的 model_data_authorized=false 保持不变。cry-babycrypto 只通过 /tmp 一次性 manifest 获准做了单次 smoke，授权已消耗；再次传输、复跑或运行 batch 前须取得新确认。
