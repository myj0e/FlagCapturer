# 阶段 A4：pilot 准入审查记录

日期：2026-09-30  
固定来源：CTFTiny `f1c9531672c45b24b7fb5f3aa44a7ac33d3602f8`  
状态：**10 道仅限私有本地 pilot 正式准入**；题目未上传到 ctfbot。cry-babycrypto 另获一次性模型传输授权并完成 smoke，该授权已消耗。

本记录只保存审查结果，不复制 flag、writeup 或挑战附件。CTFTiny 保持在 `/tmp/ctfbot-ctftiny-stage-a`，不作为 ctfbot 子模块、不把题目数据提交到 ctfbot。当前 manifest 只保留来源 commit、文件名、hash 和脱敏审查状态。

## 用户决定（2026-09-30）

- 题目只作为仓库外的本地测试源使用；不加 Git submodule，不上传 ctfbot，也不随项目分发。
- 用户确认不上传项目/不分发题目不妨碍私有本地 pilot 正式准入；该准入不包含模型数据传输，也不自动批准后续附件执行 smoke。
- 本地 gold/oracle 暂按已核实处理。若实际解题评测不通过，再针对题目条件、gold 值和 verifier 做详细判定与修复；这不是声称已经独立证明上游 oracle 的语义。
- 用户此前授权当前 manifest 声明的附件在现有 Docker 隔离 profile 中运行；若需新增 smoke，先说明具体题目和动作并征求确认。此范围不包括连接远端题目服务或向 LLM/provider 发送题目数据。
- 答案泄漏检查按一次性基本确认执行；直接/常见变换命中或明显 solution 标记仍会记录并从 agent 输入排除。

## 一次性模型 smoke 授权（2026-09-30）

- 用户批准一次真实 cry-babycrypto 解题 smoke，可将该题 allowlist 中的 ciphertext.txt 和通用 TASK.md 发送给 ChatGPT/Codex；最多 3 个模型回合、6 次工具调用。其他题目、README、solver、writeup、oracle、复跑或批量传输均不在授权范围。
- 主 pilot-manifest.json 中所有挑战仍为 model_data_authorized=false。为本次调用单独在 /tmp 生成临时 manifest 与新快照，不改变全局题目授权。题目输入与 controller-only oracle 保持分离。
- smoke 通过 exact-string controller verifier。运行中模型还读取了 workspace 根 provenance.json，该文件仅含此题 ID/category、来源/附件哈希、导入模式和授权说明；不含 flag/oracle、README、solver 或其他题目数据。这超出原定最小发送范围。已将模型文件工具和 Docker challenge mount 改为只暴露 workspace/input 子目录，并由离线 synthetic 单测验证通过；没有启动 Docker 或调用模型。

## 附件扫描结果

对固定 commit 的 26 道候选、31 个已声明附件完成一次本地隔离扫描；4 道没有本地附件。每题单独复制 allowlist 附件，挂载为只读且容器断网；报告仅保存路径、hash、命中类别、退出状态，不包含 oracle 或附件内容。没有题目数据发给模型。

- 扫描原始字节、`strings`、大小写/URL/base64/base32/hex/反转/摘要形式和标点归一化；同时检查 zip/tar/tar.gz/deb 的成员名与展开内容、PNG 文本元数据。
- 对 `.py` 与 ELF 附件做无参数 smoke；共 14 次（6 个 Python、8 个 ELF），10 次退出码为 0、4 次为 1，全部在限制内完成，无超时或输出截断。非零退出表示这次无参数 smoke 未正常退出，不代表题目已运行验证。
- `.deb` 仅在容器内列出并扫描数据，没有安装；`maze.pt` 没有加载或反序列化；不启动 Compose，不访问上游远端箱。
- 结果：19 道无明显泄漏信号；3 道有需排除/人工分类的信号；4 道无声明附件。未发现常见 oracle 形式出现在 19 道“无明显信号”候选的声明附件中。
- 另对项目树 584 个文件核验：没有发现 26 个 oracle 值的原文字节，也没有发现与 manifest 中 263 个 CTFTiny 源文件 hash 完全相同的项目内文件。审查数据本身仅以 hash/状态摘要留在项目。

| 候选 | 检查结果 | 处理 |
|---|---|---|
| `cry-ecxor` | handout 源码及其无参数输出出现两个通用 flag 样式字符串；扫描原文、`strings` 和常见变换均未匹配该题 gold | 按用户设定的基本确认门槛，作为非 oracle 信号准入私有 pilot；保留备注，真实评测出现泄漏迹象时重开 |
| `for-whyos` | `console.log` 命中 gold 原文、大小写形式及 URL 编码形式 | 排除该附件和该题，不进入 pilot |
| `msc-showdown` | tar 归档含有名称匹配 `flag/solution/writeup/answer` 模式的成员；归档内容已完整扫描，未命中 gold 形式 | 不把该归档给 agent；保留为可疑候选 |
| `rev-dockreleakage` | 文件扩展名为 `.tar.gz`、实际为未压缩 tar；按实际格式完整扫描，无明显泄漏信号 | 记录格式不一致，附件检查通过 |

其余 18 道有附件的候选同样无明显泄漏信号；逐题状态和信号摘要写入 [pilot manifest](pilot-manifest.json)。`no_obvious_answer_leak` 表示上述检查范围内没有明显命中，不等于人工证明附件绝无隐写或语义答案。

## 逐题状态

| 候选 | 工程导入 smoke | 隔离附件答案检查 | 使用范围 | gold/oracle 状态 | 当前准入 |
|---|---|---|---|---|---|
| `cry-babycrypto` | 通过 | 无明显泄漏信号；README 不提供给 agent | 私有本地，不分发 | 用户暂按已核实 | 已准入；单次模型 smoke 已获准并消耗 |
| `cry-ecxor` | 通过 | 两个非 oracle flag 样式字符串已记录；无 gold 变换匹配 | 私有本地，不分发 | 用户暂按已核实 | 已准入；有复核备注 |
| `cry-perfect-secrecy` | 通过（仅附件模式） | 无明显泄漏信号 | 私有本地，不分发 | 用户暂按已核实 | 已准入；禁止连接远端 box |
| `for-1black0white` | 通过 | 无明显泄漏信号；README/solver 不提供给 agent | 私有本地，不分发 | 用户暂按已核实 | 已准入；模型数据未授权 |
| `rev-baby-mult` | 通过（仅附件模式） | 无明显泄漏信号 | 私有本地，不分发 | 用户暂按已核实 | 已准入；禁止连接远端 box |
| `rev-rap` | 通过（仅附件模式） | 无明显泄漏信号；ELF 已做无参数 smoke | 私有本地，不分发 | 用户暂按已核实 | 已准入；附件只在隔离 runtime 中使用 |
| `rev-tablez` | 通过 | 无明显泄漏信号；README/solution 不提供给 agent | 私有本地，不分发 | 用户暂按已核实 | 已准入；模型数据未授权 |
| `rev-ezbreezy` | 通过（仅附件模式） | 无明显泄漏信号；ELF 已做无参数 smoke | 私有本地，不分发 | 用户暂按已核实 | 已准入；禁止连接远端 box |
| `msc-ezmaze` | 通过（仅附件模式） | 无明显泄漏信号；pickle 未反序列化 | 私有本地，不分发 | 用户暂按已核实 | 已准入；模型数据未授权 |
| `msc-quantum-leap` | 通过（仅附件模式） | 无明显泄漏信号 | 私有本地，不分发 | 用户暂按已核实 | 已准入；禁止连接远端 box |

## 尚未完成的准入工作

1. `for-whyos` 和 `msc-showdown` 不进入本轮 pilot；前者输入命中 oracle，后者归档成员/环境信号另行审查。`cry-ecxor` 的非 oracle 字符串已按用户门槛记录并准入。
2. 上游根目录 GPL-2.0 不能证明每项题目附件的独立许可。本次仅限用户授权的私有本地测试，不随 ctfbot 分发或公开发布题目数据。
3. 题目附件扫描不等于 Docker 宿主隔离安全认证；A3 的恶意镜像、路径/mount 越界和宿主隔离负面测试仍未完成。
4. 主 manifest 的 model_data_authorized 对全部 pilot 题仍为 false。cry-babycrypto 只通过项目外临时 manifest 完成一次性模型 smoke；授权已消耗，其他题目和复跑需重新确认数据传输范围和模型调用额度。
5. 10 个 hash-pinned workspace 已生成在 `/tmp/ctfbot-stage-a-admitted-pilot/`；只含 allowlist 附件、通用 TASK 和 provenance，oracle 与 workspace 分离。快照生成没有执行附件或调用模型。

因此，10 道题已按用户设定的“基本确认”门槛正式准入私有本地 pilot；该状态不等于 runtime 安全认证或批量真实解题 baseline 已完成。单题 smoke 状态和数据边界发现见[解题 smoke 报告](solve-smoke-cry-babycrypto.md)。
