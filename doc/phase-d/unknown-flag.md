# 未知 flag：补充提示词与模型候选

2026-10-08：TUI 和单题 `solve` 支持没有已知答案的解题。private oracle 是可选的评测辅助，不再是日常解题的必填输入。

## 使用

1. 填入单个题目文件路径，勾选 File model transfer 授权文件与工具输出发送给配置的模型；也可使用已有授权的 workspace。Preview 将单文件复制到私有 `data/imports/` 快照，不执行或修改原文件。
2. Oracle 留空；“补充提示词（可选）”可填写解题线索、已知 flag 格式或偏好的解题方法，也可以留空。
3. 运行镜像自动填写，输出目录可保留默认值；Preview 后 Run。

当前解题流程不设置默认 flag 格式，不执行格式检查。LLM 根据题目、补充提示词与解题证据判断候选，使用 `candidate_submit` 提交原始字符串；包括赛事前缀、无花括号和 Unicode 答案都可以记录，不自动修改前缀。补充提示词随本轮题目传给模型并保存在私有运行证据中，不会写回原题文件。

无 oracle 时提交结果为 `unverified`，模型仍可继续检查或提交其他候选，再用 `run_complete` 以 `candidate_unverified` 结束；证据不足时可明确以 `unsolved` 结束。`RunResult.verified` 保持 false。TUI 显示候选及“正确性未确认”，需要操作者在比赛平台提交确认。程序不自动访问比赛平台。

如果提供 oracle，则优先使用原有 exact-string verifier；补充提示词不会替代精确答案检查。提供了错误/不存在的 oracle 路径会明确报错，不静默切换模式。无 oracle 时，题目 hash/准入、模型传输授权、服务/远端 profile、预算与清理约束仍然生效。

```sh
ctfbot solve --workspace /private/challenge/workspace \
  --runtime-image sha256:<固定镜像ID> --additional-prompt '题目提示使用 RSA，Flag 前缀可能是 SUCTF' \
  --runs-root /private/runs --confirm-model-usage
```

v2 的 `candidate_unverified` CLI 退出码 0 只表示本轮已结束并记录候选，不能作为答案正确的评分信号；`unsolved`、预算耗尽或错误退出 1。历史 `format_only` 结果仍按旧版含义解释，不重写日志。查看结构化 `verified` 字段和验证方式；benchmark/evaluate 的已知答案数据集继续要求 oracle。报告保留验证方式，不复制候选原文。离线命令 replay 可以不传 oracle，但回放输出一致也不证明 flag 正确。

## 验收

当前 v2 已通过 Crypto 与单文件 ELF 的真实 Docker + Textual 验收，均以 `candidate_unverified` / `verified=false` 结束；审计、报告、命令回放及清理通过。记录分别为 `data/acceptance/20261008-common-tui-crypto-1/acceptance.json` 和 `data/acceptance/20261008-common-tui-elf-1/acceptance.json`。详见[实施记录](agent-common-improvement-implementation.md)。

以下实机记录描述早期 v1 行为；原回执保留，不将旧结果改写为 v2。自动化用例已迁移为“候选可继续检查、显式完成后拒绝执行”。

- 当前自动化覆盖任意格式候选、补充提示词传递、无 oracle 启动、候选后继续检查、显式完成后拒绝后续执行与新 turn、授权/hash 检查、显式 oracle 精确验证及严格路径检查。
- Textual pilot 覆盖 Oracle 留空、预览、运行、候选显示、Evidence 回看及不含原始候选的报告。
- 真实 Docker + Textual pilot 使用自建 Crypto 输入和确定性工具 provider，不传 oracle，并删除该用例生成的 oracle 文件；自动解析镜像 ID，容器内脚本求解，结果为 `format_only` / `verified=false`，命令回放 matched，solver 已删除。
- 私有实机记录保存在仓库忽略的持久路径 `data/acceptance/20261008-unknown-flag-1/acceptance.json`，没有使用 `/tmp`。该目录中的 generated dataset 已移除 Crypto oracle，用于无答案探索验收，不能直接作为严格 benchmark 数据集使用。
- 未调用真实模型、未运行私有题目或自动提交到比赛平台。

2026-10-08 单文件补验：自建 Reverse ELF 直接作为 TUI 输入，不提供 oracle；经显式文件模型传输授权、Preview 自动导入、真实 Docker 内分析、脚本执行与 PTY 交互后记录格式候选，结果 `format_only` / `verified=false`，报告生成、命令回放 matched、全部 solver 容器删除。记录：`data/acceptance/20261008-single-file-1/acceptance.json`。该验收使用确定性 provider，没有调用真实模型；它验证导入及工具流程，不代表任意逆向题均能求解。

复跑使用新目录：

```sh
.venv/bin/python doc/phase-d/verify_unknown_flag.py --output data/acceptance/<新的验收目录>
# 单可执行文件版本（使用另一个新目录）
.venv/bin/python doc/phase-d/verify_unknown_flag.py --single-file --output data/acceptance/<新的单文件验收目录>
```
