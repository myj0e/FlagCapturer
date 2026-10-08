# D：六类基础闭环合成验收

2026-10-07：六类 authored-v1 工作流的基础工程闭环通过。未调用真实模型、未运行私有 pilot，未证明真实题目成功率或泛化能力。

## 验收方法

`verify_workflows.py` 生成 development/holdout 各六道随机实例。确定性 provider 只通过工具读取工作流、检查环境、保存/运行容器内 Python 脚本，并从工具输出提取候选；provider 不读取 controller oracle。应用使用实际 `SupervisedOfflineRuntime`，镜像与 C4 验收相同，全部 solver 断网。

每道 development 题走完导入/授权预检、workflow、script、candidate verifier、Evidence 完整性审计、报告与离线命令回放。Reverse/Pwn 另启动真实 Docker PTY，发送输入、读到候选并关闭；回放只复现命令，不重放 PTY。

| 类别 | 代表机制 | verified | 命令回放检查点 | 额外证据 |
| --- | --- | --- | ---: | --- |
| Crypto | base64 | 通过 | 4，全部 matched | 保存脚本 |
| Digital Forensics | ZIP 小条目 | 通过 | 5，全部 matched | 提取产物、声明父输入/hash |
| Stego | P6 RGB LSB + 长度头 | 通过 | 4，全部 matched | 容器内解析脚本 |
| Reverse | 自编译 ELF XOR/input gate | 通过 | 4，全部 matched | 真实 PTY 输入输出 |
| Pwn | 自编译 ELF 结构化输入字段覆盖 | 通过 | 4，全部 matched | 真实 PTY 输入输出 |
| Web | 离线 HTTP 注释编码 | 通过 | 4，全部 matched | HTTP 响应来源；联网 HTTP 见 C4 |

所有报告确认无原始 flag。ELF 仅在容器运行，宿主只编译项目自编写的源码。该 Pwn fixture 是教学 I/O/认证字段覆盖，不等同于通用 exploit 能力。

## 受控评测与记忆

- holdout 经 ID/输入 hash 分离检查，blind 模式，固定预算 6 turns / 96 tools / 600 秒；六类共 **6/6 verified**，未执行数、环境错误、provider 错误与清理失败均为零。
- 机制已公开，provider 是固定算法；6/6 是工程验证结果，不是模型评分。费用与 usage 换算保持 unknown。
- development 和 holdout 的每次 run 显式读取 `manuals` 中同一已批准 generic 版本，`memory_used` 保存引用。条目是验收脚本内固定的一句通用建议，不含答案或题目常量，不是未经审核的真实资料导入。
- 完成后撤销该版本；新的 `MemoryView` 不再读到它，既有 frozen snapshot 保留。没有启用 challenge/writeups namespace；污染、source hash 变化、blind 禁止 writeups 等负面路径由自动化测试覆盖。
- 所有 solver 容器删除，runtime journal 无未完成项。

私有完整记录：`/tmp/ctfbot-d-acceptance-20261007-3/acceptance.json`，含六类 run/audit/replay、holdout summary、分离与记忆版本记录。之前两次验收目录保留诊断；第一次在六类回放后因 harness 的评测参数名错误退出，修正后以新目录完整复跑。`/tmp` 材料可能被系统清理，需要长期保存时另行归档。

```sh
.venv/bin/python doc/phase-d/verify_workflows.py \
  --image sha256:<本机固定镜像ID> --output /private/new-d-acceptance
```

最新全量自动化测试 **100 passed in 13.05s**。收尾另修正了评测中“验证成功后又发生环境/provider 故障”的 eligible 分母，增加两个回归用例；验证结果与故障计数均保留，避免出现 1/0。

## 阶段边界

本次完成六类基础合成闭环、受控评测、命令复现与受审通用记忆的首轮验收。阶段 D 最终目标仍需多机制/隐藏或改编样本、相关工具环境与许可证清单补足、有限真实模型评测，以及 provider usage/计费/取消语义验收。TLS/browser、PE/反编译、复杂取证/隐写和广泛 exploit 尚未交付或未验收；不将六道样例扩大为这些能力已经完成。
