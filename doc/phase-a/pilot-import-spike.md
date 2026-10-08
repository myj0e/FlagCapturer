# 阶段 A4：静态题目导入与 oracle 隔离 spike

状态：10 道私有本地 pilot 均通过临时目录导入/精确 verifier 工程检查，并已正式准入；新生成的实际快照位于 `/tmp/ctfbot-stage-a-admitted-pilot/`。这些检查不代表题目附件执行 smoke、Docker 宿主安全认证或模型解题通过。

## 本地试验结果

- Source checkout 固定在 CTFTiny commit `f1c9531672c45b24b7fb5f3aa44a7ac33d3602f8`；导入脚本要求 source commit、`challenge.json` hash 和每个声明输入的文件 hash 同时匹配 manifest。
- 输出在仓库外的 `/tmp/ctfbot-stage-a-pilot-import`。工作区只含脚本生成的通用 `TASK.md`、无答案的 provenance 和 hash 匹配的 `input/ciphertext.txt`。
- `README.md` 经人工检查发现含解题说明和 gold flag，manifest 将其标成私有；它与 `challenge.json`、`test_solver` 一并未复制进工作区。
- Oracle 单独写入 sibling `private/cry-babycrypto/oracle.json`，目录权限 `0700`、文件权限 `0600`。后续 runtime 只能挂载 `workspace/cry-babycrypto`，不得挂载其父目录或 `private`。
- 控制器侧 verifier 只输出 `VERIFIED`/`REJECTED`。本地检查了正确值匹配和错误候选拒绝；比较时未打印或记录 flag。第二次临时导入产生相同输入/oracle hash，临时副本自动清理。
- 未运行 challenge 脚本、远端服务、LLM 或题目容器。数据留在 `/tmp`，不进入版本库。
- 2026-09-30 对 10 道 shortlist 做了临时批次导入；总附件 83,899 bytes。10/10 都检查了输入 hash、workspace `0700`、input 目录 `0555`、input 文件 `0444`、oracle sibling 目录 `0700`/文件 `0600`、正确 candidate 通过和错误 candidate 拒绝。6 道 `offline_artifact_only` 的 TASK 均带禁止联网说明。
- 10/10 又各重复导入两次；逐文件输入 hash、provenance、TASK 和私有 oracle 文件 hash 完全一致。临时数据在检查后清理。
- shortlist 的 13 个输入只做字节头/UTF-8 可解码检查，未调用文件格式解析器：3 个 ELF、2 个 PNG、8 个 UTF-8 文本；总量 83,899 bytes。`maze.pt` 保持不加载、不反序列化。
- `cry-perfect-secrecy` 未带 offline-only 开关、`pwn-puffin` 带该开关、含答案的 `for-whyos` 输入、以及 README 输入未审的 `rev-whataxor` 均被拒绝。批次临时目录已自动清理，oracle 值没有打印或发送给模型。
- 导入器仅对 `cry-perfect-secrecy`、`rev-baby-mult`、`rev-rap`、`rev-ezbreezy`、`msc-ezmaze`、`msc-quantum-leap` 支持显式 `--offline-artifact-only`；该模式没有远端访问工具。
- manifest 的精确 oracle 字节扫描发现 `for-whyos/console.log` 含 gold 值；该附件标为私有并从 shortlist 排除。这个检查只阻止直接包含答案的附件，不代替人工题意/隐性泄露审查。
- 对项目中 60 个 tracked/untracked、非忽略文件扫描了 26 个候选的精确 oracle 字节；未在项目工作树发现答案值。扫描结果只输出通过状态和计数，不输出 flag。
- importer 限制每个输入文件不超过 32 MiB、全部输入合计不超过 64 MiB；输出目录必须在项目仓库和源 checkout 之外。当前只校验哈希/路径并复制附件，不解析或执行不可信文件。

## 复现

```sh
python3 doc/phase-a/spikes/pilot_import.py \
  --source-root /path/to/local/CTFTiny \
  --manifest doc/phase-a/pilot-manifest.json \
  --challenge cry-babycrypto \
  --output-root /tmp/ctfbot-stage-a-pilot-import
```

对 allowlist 中声明上游 box 的附件候选，还必须显式增加 `--offline-artifact-only`。此模式没有远端访问工具，且会在生成的 TASK 中写入禁止联网说明。

为避免意外覆盖含有 oracle 的旧快照，`--output-root` 必须是尚不存在的新目录。工具拒绝符号链接、越界路径、缺失文件、hash 漂移和大于 32 MiB 的单个输入。模型/执行容器只接收 workspace 路径；控制器单独运行 verifier：

```sh
cat /path/to/candidate.txt | python3 doc/phase-a/spikes/verify_pilot_candidate.py \
  --oracle /tmp/ctfbot-stage-a-pilot-import/private/cry-babycrypto/oracle.json
```

候选通过 stdin 输入。不要把 oracle 路径、内容或所在父目录暴露给 agent；此 helper 是本地 spike，尚不是生产安全边界。

## 限制

- 10 道 pilot 都通过导入器工程检查；之后 26 道候选的 31 个声明附件已在断网 Docker 中做过一次答案泄漏扫描，具体命中见[准入审查](admission-review.md)。用户暂按 gold/oracle 已核实处理，实际解题失败时再详细复核；正式准入仅限私有本地使用，不构成逐项许可证明或宿主隔离安全认证。
- 用户已确认本地导入/评估授权，但上游各题资产没有逐项独立 license 文件证据；禁止分发仍然适用。
- 用户授权现有附件在 Docker 隔离环境运行，但没有授权将题目数据发送给 LLM/provider；`model_data_authorized` 继续为 `false`。
- 通用任务说明不包含原始竞赛叙述。要做有意义的解题评估，需要单独审核并撰写不泄漏答案的 prompt。
- 该分离依赖后续 runtime 只挂载明确 workspace 子路径；不能把 `/tmp` 下共同父目录整个挂载给容器。
