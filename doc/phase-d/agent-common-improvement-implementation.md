# Agent 通用可靠性：首版实施与验收

日期：2026-10-08。依据：[通用改进计划](agent-common-improvement-plan.md)。

G01–G06 的首版工程契约已实现。工程回归和合成 Docker/TUI 闭环通过；没有调用真实模型、执行私有题目或向比赛平台提交候选。原 RSA 运行仅作为复盘依据，答案、常量及题解没有进入通用提示、公共 fixture 或共享记忆。本增量不代表阶段 D 最终验收完成，也不替代 Reverse 专项 V 系列任务。

## 1. 从记录候选到明确结束

新 run 使用 `result_contract_version=2`，工具和 pack 快照记录通用契约版本。

| 动作/状态 | 含义与结束行为 |
| --- | --- |
| `candidate_submit` | 记录候选、来源、推导与检查引用；无检查可填写 `unchecked_reason`。返回 run-local candidate ID |
| 工具 `format_only` / `format_mismatch` | 仅说明格式；不终止工具或新 turn，不证明答案正确 |
| `candidate_check` | 运行 sandbox 内的局部关系检查；失败后仍可继续，成功也不提升为 verified |
| `run_complete(outcome="candidate_unverified", candidate_id=...)` | 引用本次已记录候选，明确结束为未验证候选 |
| `run_complete(outcome="unsolved", ...)` | 明确未解出；不需要 flag，也不证明数学上无解 |
| exact oracle `verified` | controller 独占的可信精确验证，仍可立即结束 |
| 预算、取消、provider/runtime 失败 | 继续保持独立状态；已有格式候选不会掩盖预算耗尽或运行失败 |

显式完成要求非空 `summary` 和 `unresolved` 数组；不接受模型自报 `verified`。完成后拒绝后续工具，不再启动新 turn；provider 中断不覆盖已接受的完成。缺少完成信号时提示核对公开事实、假设和下一步实验，同时允许明确未解出，禁止为结束而捏造 flag。兼容无工具自然结束：无候选仍为 `unverified`，有候选为 `candidate_unverified`。

CLI：`verified` 和 `candidate_unverified` 退出 0，后者只说明尝试已完成并留下候选；`unsolved`、`unverified`、预算耗尽与错误退出 1。历史 `format_only` 退出 0 的解释保留，旧日志不重写。严格 benchmark 的成功仍只计可信 `verified`，局部检查及未验证候选均不计为成功。

TUI 实时记录和 Evidence 保留候选原文与状态；基础报告和诊断索引只显示状态、hash 和私有引用，不复制候选原文。

## 2. 来源、覆盖范围与聚焦读取

每次实际工具调用产出 run-local `observation_id`，绑定 run、镜像、输入 provenance 与调用上下文。原始工具回复、底层 stdout/stderr、模型实际收到的回复分别可追溯；模型回复有大小限制，原始 runtime 输出也可能已经截断，不能承诺无限保存。摘要记录覆盖/过滤条件；摘要没出现某值不代表原始数据不存在。

| 工具 | 边界 |
| --- | --- |
| `artifact_read` | 仅本次 EvidenceStore 已产生且 hash/大小正确的私有普通文件；单 artifact 读取上限 4 MiB，每次范围最多 4096 字节 |
| `challenge_read_bytes` | 仅已授权输入的普通文件；输入最多 32 MiB，每次最多 4096 字节；有准入 hash 时复核 |
| `observation_get` | 读取历史观察的上下文和证据引用，不产生新的网络请求，也不将历史响应当成当前服务状态 |

offset/length 均为文件**字节**，不接受虚拟地址语义。UTF-8 边界可能用替换字符显示；需要精确字节时使用 hex。路径授权、预算、镜像和网络 grant 不变。旧 run 缺少这些字段时诊断明确列为缺失，不补造历史证据。

## 3. 假设与实验绑定

`claim_record` 保存简短公开结论、假设状态、引用与未知项；不采集隐藏推理。`supported` 只表示模型报告支持，始终 `controller_proven=false`。来源缺失或关联实验存在来源冲突时，不能升级为 supported。

`experiment_record` 必须引用本次真实执行工具产生的 observation，记录实际参数、转换、目的、输出判断及绑定。单个绑定示例（ID/hash/path 必须替换为实际值）：

```json
{
  "observation_id": "observation-1",
  "artifact_path": "artifacts/<内容寻址文件>",
  "offset": 0,
  "length": 4,
  "sha256": "<所选4字节的sha256>",
  "locator": "source byte range",
  "parameter": "copied_value",
  "representation": "utf8"
}
```

controller 验证 artifact 属于关联 observation、字节 hash 一致；指定 `parameter` 时核对 `parameters.copied_value` 是否等于所选 UTF-8 文本或 hex。复制错的参数即使脚本 exit 0，也产生来源不一致。转换后的参数可记录转换说明，但不因此自动证明其语义正确；没有绑定的硬编码实验保留假设实验等级。来源一致不是任意数学证明或完整数据流证明。

## 4. 局部检查与错误分类

`candidate_check` 首批支持自建输入的 `base64_equals`、`json_field_equals`（RFC6901 指针）和 `http_body_equals`（原始 body 等值，不以 HTTP 200 为成功）。输入上限 1 MiB，固定版本 checker 在原 sandbox 中执行，复核源 hash，记录 checker 版本/hash、参数和原始执行证据。局部检查仅覆盖所声明关系，不是通用 flag oracle。引用检查时核对候选字节 hash，不能把另一候选的通过记录转移过来。

输入列表添加 ELF/PE/UTF-8/binary 启发式提示；workflow 描述声明格式预期。工具 schema 在调用前检查。错误区分无效参数、输入路径/格式、源 hash 不一致、API 使用错误、依赖缺失、超时与执行失败，保留私有原始异常。错误分类包含启发式判断，不自动安装包、扩大网络或将环境错误计为模型能力结论。复杂解析仍在 sandbox 内。

## 5. 实际编排记录与只读诊断

```sh
ctfbot diagnose-run --run-dir /private/runs/<run-id>
```

命令只读：先验证 artifact 完整性/事件次序，再输出 JSON 索引，不执行题目、启动 Docker、调用模型或修改日志。索引关联每轮实际 controller prompt、工具 schema、公开模型消息、工具输入、原始返回、模型可见返回、覆盖范围、候选/检查、claim/experiment 和主控继续/结束原因。原文留在私有 artifact，不将凭据、候选或公开消息原文另行复制到索引。

重复调用按工具名及参数 hash 汇总，仅标记待复查，不把合理轮询和重试自动判为无效探索。旧日志的缺失 prompt/provenance 会明确标记。损坏、不完整或超过审计边界的日志可能拒绝诊断；artifact 完整性校验不是数字签名或来源真实性证明。

评测新增来源引用、检查覆盖、来源冲突、显式完成和重复动作指标。模型自报支持的结论单列；这些工程指标不等同于解题率或费用收益。

## 6. 验收证据

- 自动化回归：`.venv/bin/python -m pytest -q`，182 项通过（39.84 秒）；覆盖现有路径及新契约。
- `data/acceptance/20261008-agent-common-2/acceptance.json`：真实 Docker，Base64、JSON、离线 HTTP 三条“假候选检查失败 → 继续 → 正确局部关系检查 → 未验证候选完成”路径，以及明确 unsolved；错误复制参数检出，报告/诊断/审计通过，三条命令回放 matched，资源清理完成。
- `data/acceptance/20261008-common-tui-crypto-1/acceptance.json`：真实 Docker + Textual，无 oracle 的 Crypto 流程，候选、Evidence、报告、审计、命令回放和清理通过。
- `data/acceptance/20261008-common-tui-elf-1/acceptance.json`：自编译单文件 ELF 的导入/执行/PTY 与无 oracle 结束，同样通过。
- 首次 common 验收发现实验绑定的 `artifact` 字段被审计误当作完整 artifact 引用；改为选择器字段 `artifact_path` 后补回归并复跑通过。失败记录 `20261008-agent-common-1` 保留。

以上 provider 均为确定性自编写实现，证明工程契约可运行；不证明真实模型会主动核查，也不代表任意题目均能求解。重启 TUI 可加载新工具契约；本增量无需重建工具镜像。

## 7. 下一轮真实模型对照方案（尚未执行）

取得具体题目传输授权和模型总预算后，固定模型/provider/镜像、各题 turns/tools/wall-time 上限、记忆策略和重复次数，比较改动前后；保留代码版本与配置。至少两个非 ELF 机制并包含 ELF，开发集和 holdout 使用分离实例，题型/机制分别报告；不能用原 RSA 私有答案构造公共测试。

同时记录可信正确率、未验证/未解出/预算/环境/provider 失败及未运行分母；另报候选来源引用率、实际检查覆盖率、来源冲突、正确停止和重复动作。缺少 checker 的样本单列且保留在评测分母；未知 usage/价格不按零成本处理。先用小开发批次调整，再冻结配置评估 holdout；检查收益是否只是预算或工具数量增加。结果没有完成前，不声称解题率或泛化提升，不自动写入跨 run 共享记忆。

## 8. 第二轮更新：调用计数与低成本观察（2026-10-08）

按操作者最新要求，**日常解题不再设置工具调用次数上限**，而非把 60 改成一个更大的数。`RunLimits.max_tool_calls` 默认 `None`，TUI 运行状态只展示 `tool calls N`。单题 solve 和 baseline 默认同样无限计数；运行快照保存 `max_tool_calls: null`。墙钟时间、模型轮次、输出大小、session 和授权边界继续有效；无限调用计数不表示无限运行时间。

为兼容已有自动化和受控评测，显式 `--max-tool-calls N` / `RunLimits(max_tool_calls=N)` 仍可设置正整数额度，取消原来的最大 60 校验。`evaluate --max-total-tool-calls N` 是操作者明确指定的整批评测预算，仍执行；日常 TUI 不使用这个整批预算。未设置单题额度时，evaluate 按整批剩余额度分配，不会对 `None` 做数值比较。

### 已更新的功能

- 回复新增 `run_budget`：调用计数、可选显式剩余额度、剩余时间与输出额度；执行/观察/控制请求由 controller 分类，记录 requested/admitted/rejected。admitted 表示允许进入处理，不等同于实际计算成功或执行次数。精确执行事实仍由 command_execution/session/request 证据确认。极小回复空间可能省略预算字段，完整信息见事件与快照。
- `run_complete` 和仅停止本 run 资源的 `session_close` 不被求解调用/输出/时间闸门挡住；完成摘要使用有界工具回复空间。保存摘要时如果已发生预算耗尽，最终状态仍为 `budget_exhausted`，模型报告的 outcome 独立保存。取消、sandbox 丢失仍优先；provider 已结束后不追加模型调用来补摘要。
- `challenge_read_bytes` / `artifact_read` 返回 run-local `source_ref`，可直接用于 experiment binding。服务端验证引用和不可覆盖的 artifact 坐标；原附件偏移与选中字节 artifact 偏移分开。旧手工字段与 `parameter` 继续兼容，推荐明确的 `parameter_key`。错误指出具体 binding 与参数键用法。
- `session_read` 增加可选 `collect_seconds`（0–60）；合并心跳，遇到终态、错误、输出上限或运行 deadline 提前返回。默认旧行为保持；新提示建议后台任务使用 30 秒合并观察。底层仍每次最多等待 10 秒，可由 runtime 取消/关闭唤醒；读取不重置真实输出静默时间。
- 通用提示与六类 playbook 要求廉价检查优先、先核对能力、昂贵库步骤各自有界；前台 timeout 是已结束的执行，后台运行必须关联真实 session。工具返回增加明确执行状态。TUI 精简展示不展开新增预算元数据。

简化来源绑定示例：先读取并取得实际 `source_ref`，再登记真实执行的 experiment：

```json
{
  "source_ref": "source-1",
  "parameter_key": "cipher",
  "representation": "utf8"
}
```

此处 `cipher` 必须是本次 `parameters` 字典的键；绑定引用的 observation 自动加入来源列表。可复用同一 source_ref，但未实现通用参数集对象或自动证明脚本实际使用参数。

### 验收与保留范围

自动化新增超过 60 次默认调用、显式额度后的完成摘要、两类非 ELF 输入的简化绑定/错误修正/坐标篡改拒绝、合并心跳及终态。完整回归 188 项通过（36.24 秒）；最终报告/诊断相关变更另有聚焦回归。Docker 超过 60 次调用回执：`data/acceptance/20261008-agent-common-unlimited-2/acceptance.json`，Crypto 76 次工具调用，JSON/离线 HTTP 各 11 次，unsolved 路径 4 次；候选与终态、审计、报告、诊断、回放和清理均通过。真实 Docker + Textual 回执：`data/acceptance/20261008-unlimited-tui-1/acceptance.json`，无 oracle 的运行/报告/审计/回放通过，资源清理完成。均未调用真实模型。

G07/G08 已交付本次要求的计数遥测与预算后摘要通道；按最新要求不增加执行/观察次数配额。G09/G10 为首版：尚缺参数集对象、聚合所有 binding 错误、超过 120 秒任务的“不超过 6 次读取”完整专项验收。G11 的规划提示和 G12 的状态字段已接入，但没有新增通用分阶段执行器，也没有自动判定自然语言与执行状态矛盾的诊断器。G07–G12 的全部原验收不能因此标记完成；真实模型对照仍未执行。


### TUI 解题摘要侧栏

运行记录使用约 75:25 双栏，F3 可隐藏摘要；宽度小于 110 列时上下排列。摘要与左侧记录独立滚动。`summary_update` 更新完整公开摘要，包含目标、最多 5 条带 observation 引用的模型报告线索、最多 3 条假设、方案与公开依据、最多 2 项下一步、阻塞点和更正。来源编号只确认运行内引用存在，不证明自然语言结论。

同一 provider turn 可能包含多段公开说明及大量工具调用，因此更新按“新公开说明或新工具结果”控制，拦截无新进展的重复刷新，不按 provider turn 硬性限为一次。没有额外总结模型请求。摘要版本通过 hash artifact 和 `summary_updated` 事件保存，TUI 标注更新时间、轮次、版本与未更新状态；Evidence 回看可按事件顺序恢复。新预览或新运行清空旧摘要。结束时保留摘要、运行状态及最近候选，复制仍通过 Flag 窗口。

自动化覆盖来源引用与长度边界、重复更新抑制、同轮新结果后更新、完整替换与撤回、实时/回看一致性、F3、75:25 布局与窄屏布局、证据损坏时保留旧摘要、控制字符显示和重置。模型是否及时调用摘要工具仍取决于提示词遵循；未调用时显示等待，不伪造摘要。
