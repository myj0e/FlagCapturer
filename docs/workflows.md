# 证据驱动的 workflow

Pi 负责推理、会话、工具循环和压缩。Workflow 提供可检索的方法、受约束的确定性操作，以及与现有证据系统连接的产物链；没有第二套 agent 循环、固定 DAG 或必做检查清单。简单题可以直接检查、提交候选并完成报告。

## 方法检索

`workflow_discover {}` 返回六类入口、弱文件名提示、方法索引和原始 asset 引用。分类不限制方法匹配，文件签名也只是线索。

```json
{"query":"PNG PDF 魔数不一致","limit":3}
```

传给 `workflow_search`；可另附最多 8 个 `observation_ids`，使用其有界摘要补充匹配。检索采用可解释的词项和中文双字匹配；命中返回匹配词，不输出伪造的置信概率，也不推断环境能力。内置方法与显式受审 memory 共用目录，来源分别为 `builtin-method` 和 `reviewed-memory`。旧 `memory_search/read` 保留。

`workflow_read {"id":"method/format-views"}` 默认读取摘要；增加 `"detail":"full"` 读取前提、排除条件、最小实验、支持/反驳/不确定结果及后续分支。受审记忆使用 `memory/<原ID>`，保留来源和许可，不自动变成可执行方法。

16 张内置方法位于 `assets/methods/catalog.json`，按机制跨题型整理。描述为本项目原创归纳，附原始题解链接，均标记 `source-derived`；没有声称原题已复现，不包含固定 flag、地址、偏移或完整题解脚本。新增方法需通过加载时 schema 校验并使用唯一版本化 ID。

`workflow_environment` 指定 `method_id` 或旧 `pack`，检查实际二进制路径、版本输出、方法要求的 Python 模块可导入性及版本。缺少 pack 的可选工具不代表核心能力缺失；`unmet_required` 单独说明。模块导入成功不证明 API 兼容，缺少版本信息不会伪称已核实。检查不安装软件、不改变网络权限。

## 产物链

原始附件自动进入 `state_read {"section":"assets"}`。派生产物记录 SHA256、大小、父 asset ID、转换参数和版本、执行证据及完整性说明。声明的转换语义仍是报告，哈希只确认捕获的字节。

```json
{"pack":"crypto","operation":"decode","asset_id":"assets-1","parameters":{"encoding":"hex"}}
```

将上例传给 `workflow_run`，结果中的 `output_assets` 可直接作为下一次操作的输入。支持三个互斥选择器：原有 `path`、单个 `asset_id`，或用于重组的 `asset_ids`。`parameters` 按具体操作再次校验，拒绝未知参数。

`asset_read` 提供最多 4096 字节的有界读取，返回覆盖范围和可供实验绑定使用的 `source_ref`。脚本产生的文件通过以下调用捕获：

```json
{"path":"/work/result.bin","parent_asset_ids":["assets-2"],"transformation":"脚本解密输出"}
```

传给 `asset_capture`；后续引用保存的内容，不重新读取可能被修改的工作路径。派生产物上限 16 MiB，原始输入仍沿用原有 32 MiB 限制。运行时重新物化派生文件，并在操作内核对预期哈希。`artifact_export` 保留原有检查点返回，并增加 `asset_id`。

通用操作：

| 操作 | 契约 |
|---|---|
| `crypto/decode` | `encoding` 为 auto/hex/base64；严格解码，保留多种合法解释，完整结果保存为 asset |
| `forensics/triage` | 签名偏移和有界字符串，内嵌签名不代表有效格式 |
| `forensics/archive` | ZIP 中央目录清单，返回可用于提取的 entry index |
| `forensics/extract` | 指定 `entry_index`，只提取一个成员，校验大小和 CRC；归档名称不用于生成路径 |
| `forensics/slice` | 指定 offset/length，必须完整落在输入内 |
| `forensics/reassemble` | 显式 asset_ids、positions、expected_parts；位置必须覆盖 0..N-1 且不重复 |

ZIP 提取支持 stored/deflate，拒绝加密、符号链接、目录成员、ZIP64/多卷以及超过输出上限的内容。复杂格式仍可由已有沙箱脚本和工具处理，再捕获产物。重组只验证位置齐全与字节拼接，顺序的语义正确性由独立结构或原检查器验证；重复内容提示但不直接拒绝，因为不同位置可能合法包含相同字节。

原 PNG、PPM、ELF、HTTP、RSA、反汇编操作保留。PPM 的固定布局假设在契约中明示；RSA 小因子辅助仅在确认两个不同的小素数且指数可逆时解密，并检查重加密关系，不把无小因子等同于安全。更一般的参数退化和大整数检查由方法卡引导沙箱脚本完成。

## 结果与决策

操作返回 `observed`、`partial`、`not_found`、`inconclusive`、`unsupported`、`missing_capability` 或 `failed`。`not_found` 只代表指定检查未发现结果；`partial` 必须结合覆盖范围判断。完整执行的转换也可能只展示截断预览，完整 bytes 由产物保存。完整 stdout/stderr 始终保留为证据，`result` 提供结构化结果。

相同操作、输入内容和参数再次执行时，返回既往 observation ID 和提示；不禁止重复。`retry_reason` 用于说明环境变化、随机性或时序验证。它不是成功保证。

复用 `claim_record`、`experiment_record`、`summary_update`：

- 实验可关联 `method_id`、`hypothesis_ids`、`expected_observation`、`next_action`、`retry_reason`，自动保存方法版本。
- claim 可用 `supersedes` 关联被修正的旧判断；历史记录不覆盖。
- 摘要可记录 `achieved_capabilities`、`remaining_gap` 和 `blocker_kind`。
- 阻塞类型为 `evidence_missing`、`prerequisite_unknown`、`hypothesis_rejected`、`capability_missing`、`composition_unclear`。

这些字段用于路线选择、耗时实验或重复失败，不要求每个普通读取先填写实验。记录公开、可审计的事实与实验依据，不记录私有推理。

候选检查支持原始 path 或 asset_id，新增 `bytes_equals` 检查精确字节关系。通过局部检查不等于平台接受。原立即提交候选、继续实际剩余目标、显式保存 writeup 的完成契约保持一致。

宽屏工作台侧栏显示当前方向、最近操作状态、近期 asset 引用和首个阻塞原因。概览通过 `/state` 查看，产物与实验可用 `/state assets`、`/state experiments` 或 `/state summaries latest` 读取，单次展示最多 20 条；agent 使用 state_read 的游标继续分页。窄屏同样通过实验事件查看状态与产物引用。命令回放会映射派生 asset ID；网络、模型决策和任意脚本内硬编码的旧运行路径不保证可复现。
