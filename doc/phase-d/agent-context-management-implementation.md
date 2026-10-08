# Agent 上下文管理实施记录

日期：2026-10-08。对应[设计方案](agent-context-management-plan.md)。CM01–CM06 的代码与确定性验收已完成；真实模型压缩效果对照未执行。之前的 oracle 和重复原型清理继续适用，所有候选均未经答案验证。

## 模块与职责

| 模块 | 职责 |
| --- | --- |
| `agent/context.py` | 当前运行状态索引、版本绑定分页、8 KiB 快照；引用原有领域记录 |
| `tools/reliability.py` | 状态工具契约、摘要历史、观察/来源绑定；恢复读取不算求解进展 |
| `tools/registry.py`、`tools/domain.py` | 可查询脚本与会话登记、执行引用、显式导出检查点；会话状态带观测时间 |
| `tools/presentation.py` | 唯一的有界 JSON 呈现规则；保留状态、资源 ID、证据引用与文本头尾 |
| `agent/budget.py` | 普通、恢复、收尾额度及实际传输计数；独立记录 prompt/参数/公开说明 |
| `model_adapters/events.py`、`mailbox.py` | 已确认 schema 的事件归一化、通知合并、有界队列和公开文本缓冲 |
| `agent/context_events.py` | 将回调排队，顺序处理压缩 epoch、累计账单 delta 和状态补发 |
| `agent/loop.py` | 准入与截止时间、调用上述组件、续轮/回复注入和证据记录 |
| `reporting`、`tui/timeline.py` | 展示传输语义、占用未知、已观测压缩和实际补发；旧日志缺失项保持 unknown |

未增加第二套实验或候选数据库。索引的 revision 单调递增，索引 artifact 保留 IDs；原始领域事件和 hash artifacts 保留完整记录。`state_read` 自身不制造 observation、不使分页 cursor 过期。其他证据读取可记录 observation，仍不允许借此反复刷新摘要；新轮次也不单独算求解进展。

## CM01–CM02：状态找回与快照

`state_read` 提供 `overview`、`summary`、`observations`、`claims`、`experiments`、`candidates`、`scripts`、`sessions`、`checkpoints`。使用 `id` 精确读取或 `cursor` 分页，两者互斥；摘要支持 `id="latest"` 或版本字符串。每页最多 20 条，UTF-8 JSON 不超过 8192 字节。大型记录返回完整 record artifact 和明确的 omitted_fields。

cursor 绑定 run、section、revision、offset。不同运行、错误分区、失效版本、无效编码和越界位置均返回错误。只有当前运行登记过的证据可读取；缺失来源显式标记。`present_hash_unchecked` 只说明文件存在，真正读取时仍检查长度和 SHA-256。

脚本记录包括 source/hash、保存调用上下文和最后执行观察。session 记录包含 ID、类型、参数摘要、观测时间及 live/historical/unknown 标记；live 仅表示该时间点的实际读取结果，不能推断现在仍在运行。枚举不读取或消费进程输出，异步退出后记录保留为 historical。检查点只来自 `artifact_export`，父输入按本轮准入 hash 检查，不扫描任意 `/work` 文件。

首轮分开稳定控制规则与题目/用户提示。后续轮次提供不超过 8 KiB 的 controller 快照，包括原题 artifact、授权范围、最新模型摘要、资源与证据索引、预算和省略分区。摘要标为模型报告，不是证明。完整题目和省略记录可通过工具取回。快照 artifact 记录 reason、revision 和实际字节数；注入的完整 prompt/reply 另行落盘。

## CM03：长输出恢复

执行结果分别呈现 stdout/stderr 的头部和尾部，保留退出状态、资源编号与完整证据。JSON 按字段省略，不能容纳的嵌套记录回到 artifact 读取，不提供半截 JSON。loop 在预算缩小时使用完整 presentation source 再呈现，避免嵌套 preview。

预览的 omitted_byte_range 属于解码后的 UTF-8 文本坐标；原始非 UTF-8 输出可能经过 replacement，精确二进制定位应读取 stdout/stderr artifact。原始字节与模型实际看到的回复分别保存。证据读取上限为 32 MiB，以覆盖控制字符导致的 JSON 膨胀；单次返回仍最多 4096 原始字节。

- `artifact_read(artifact, offset, length, encoding)`：有界字节读取，encoding 为 utf8 或 hex。
- `artifact_tail(artifact, length, encoding)`：最后最多 4096 字节，返回实际 offset、长度和 source_ref。
- `artifact_find(artifact, literal, offset, scan_bytes)`：字面 UTF-8 匹配；扫描最多 1 MiB，返回最多 20 个字节位置、扫描内命中总数、省略数量和 partial_scan。没有正则或任意文件路径权限。

## CM04：预算

旧 `total_model_output_bytes` 保留，明确代表普通工具回复传输额度，默认 256 KiB；独立恢复池默认 32 KiB，收尾池默认 8 KiB。单次回复默认最多 16 KiB。普通额度达到 80% 后回复缩至最多 2 KiB，并带额度提醒。

`state_read`、`observation_get` 和聚焦 artifact 读取只使用恢复池；`run_complete`、`session_close` 使用收尾池。普通命令不能借用恢复池。普通额度不足时拒绝执行，拒绝文本也计数；无法再容纳拒绝文本时使用有限收尾池，耗尽后不继续传输无界错误。运行资源清理由既有生命周期保障。

`tool_reply_bytes` 只统计真正构造给 adapter 的 UTF-8 工具回复；prompt、参数和公开说明分别计数。回复中的 context_restore 使用恢复池，prompt 中的恢复也消耗恢复池并单独统计 prompt_restore_bytes。压缩不重置额度。默认工具调用无次数硬上限；已有显式次数配置和 wall time 仍限制非收尾工具；恢复池不能绕过它们。沙箱准入约束继续执行。

字节额度不是上下文 token 占用。报告包含版本、语义、各池用量与剩余量；旧日志不存在遥测时不补造数值。

## CM05–CM06：provider 事件与压缩恢复

本机 `codex-cli 0.159.0-alpha.3` 的 `generate-json-schema --experimental` 已确认：

- `thread/tokenUsage/updated`：`tokenUsage.last`、`total`、可空的 `modelContextWindow`。
- `item/started` / `item/completed` 的 `contextCompaction` item（有 ID）。
- 已弃用的 `thread/compacted` 通知（无 item ID）。

`last` 是最近一次 inference 的用量，不冒充窗口占用。累计 `total` 用 high-water delta 统计，重复/较旧数值不重复相加；容量来源单独记录。当前占用仍 unknown，不显示 0% 或由账单推算百分比。缺少事件的版本依赖 state_read 与续轮快照。

传输与待处理队列最多 512 条、4 MiB，usage 可按 thread/turn 合并。工具 RPC、turn 终态、公开消息和压缩事件不被普通通知淘汰；关键消息无法安全保留时显式报错。当前 turn 的无关通知被消费，未知 server 请求按协议回复错误；处理过的请求不继续滞留。controller 回调队列另限 128 条，只有 usage 合并，溢出也显式失败。公开最终文本缓冲有界，完整公开消息由证据回调保存。

只有匹配当前 thread/turn 的事件进入归一化桥接。压缩完成置 pending_restore，下一次普通工具回复附独立 context_restore，或下一次续轮 prompt 提供快照。相同 item ID 去重，连续 epoch 合并为最新；现代与旧通知的同轮别名也去重。旧通知没有可区分的 epoch，保守按同轮一次处理。

实际包含恢复内容后才记录 context_restore_delivered；额度不足、没有后续工具/轮次时保持 pending。此事件记录向 adapter 注入的内容，不代表远端接收确认或解题效果。没有主动压缩命令、异步插话接口或模型线程重启。

## 验收

- 新增 `tests/test_context_management.py`：29 项通过，包括遗忘 provider、1000 条记录、失效 cursor、缺失来源、跨轮摘要防空转、会话无副作用枚举、二进制/Unicode/巨大单行、尾部答案/错误、独立预算、拒绝轮询、累计 usage、现代/旧压缩去重、未送达状态和混合 RPC 长流。
- 完整回归：209 passed，1 failed。剩余失败是既有 `test_command_timeout_preserves_work_and_cleans_detached_descendants`：本云环境缺少 `/proc/<pid>/task/<pid>/children`，触发 supervisor 异常和模拟 Docker kill 的后续 IndexError。没有屏蔽或改变该测试。
- 静态检查：Ruff F821/F822/F823/F401/F841 和 `git diff --check` 通过。
- 真实 Docker：`verify_context_management.py` 9 项检查通过；覆盖真实脚本/session ID 找回、枚举保留输出、显式检查点取回、关闭状态、stdout/stderr 尾部、快照边界和清理。回执：`data/acceptance/context-management-final-20261008/acceptance.json`。

复现 Docker 验收：

```sh
.venv/bin/python doc/phase-d/verify_context_management.py \
  --image sha256:6745f679cd1ebe292127fae56c3a0739a9deeb036df01ea4ac9377ec7367e69c \
  --output data/acceptance/context-management-new-run
```

使用自建数据，未调用真实模型、未使用私有题目。真实模型的压缩事件效果、长题解题率对照尚未验收；schema 和合成通过不替代这项证据。跨进程续跑、自动恢复沙箱/模型线程仍在本次范围之外，`resume_supported` 保持 false。
