# C4：受控 TCP 合成实机验收

2026-10-07：首个受控 TCP 闭环通过合成验收。未调用真实模型、未使用私有题目或外部服务。

## 环境与范围

- Docker 29.8.1，本机非 root controller，固定离线 solver 镜像 `sha256:4c0b5187de4f9146b628cdeb6f951f0ba8c294ab35f539a953a20f6d7b282fe6`。
- controller 自建 `127.0.0.1` TCP endpoint；请求 hostname 使用 `authored.invalid`，独立 grant 固定 IP，不查询 DNS。授权窗口仅十分钟。
- solver 使用 `--network=none`；TCP/HTTP 由 controller 受控 connector 连接。没有启用容器通用出网。
- provider 为确定性工具驱动实现，从 TCP 工具响应获取候选，提交 controller verifier；不读取 oracle。

## 结果

全部 **28 项 REQUIRED_CASES** 通过，另有 HTTP 路径绕过拒绝检查，共 29 项。

| 范围 | 实测结果 |
| --- | --- |
| 解题与证据 | TCP 获取候选、exact verifier、报告与 artifact 审计通过；报告不包含原始 flag |
| 授权范围 | 错误 host/port/protocol、literal IP 与 pin 不符、缺失 grant、过期 grant、超额输入被拒绝 |
| 输出与 HTTP | 响应限制在指定字节数；302 不跟随；proxy 环境被忽略；authority、CRLF、反斜杠路径被拒绝 |
| DNS 与容器路径 | 禁用 controller `getaddrinfo` 时仍能通过 pin 解题；真实 command/PTY 中检查无 IPv4/IPv6 默认路由，loopback、宿主、公网和 DNS 探测被拒绝/无外部回答 |
| 连接生命周期 | tool/run 超时、实际 scope 到期、取消、连接拒绝均关闭连接；provider 初始化失败返回明确状态并清理 |
| Docker 回执 | create/start 操作已生效而回执延迟时，前台超时保留 intent、阻止新 run，迟到回执后按 owner 清理 |
| controller 崩溃 | 子 controller 启动真实 solver 后直接退出；父进程 recovery 删除容器并完成记录 |
| 清理失败 | 对 controller `remove_owned` 注入异常，保持未完成与 admission 阻断；解除注入后真实 Docker recovery 成功 |
| 回执丢失 | create 已生效后终止独立回执 worker；删除已知 owned 容器后仍保留 pending intent，继续阻断 admission |
| 产品入口 | 显式候选 factory 和临时受审 factory 下，真实 solve CLI 与 Textual pilot 均 verified；TUI 显示候选，报告不泄露候选 |
| 回归 | A/B/C 自动化回归 88 项通过，其中新增 C4 连接测试 14 项 |

scope 到期用例将测试 runtime 的有效截止时间缩短；cleanup failure 是 controller 故障注入，不声称真实 daemon 故障覆盖。DNS 检查证明连接不依赖解析；未运行外部恶意 DNS 基础设施。PTY 回放仍是单独能力。

## 私有记录

- 完整汇总：`/tmp/ctfbot-c4-acceptance-20261007-5/acceptance.json`
- 启用契约：同目录 `activation-acceptance.json`、`boundary-cases.json`
- 受审入口补验：同目录 `activation-validation.json`
- Docker 恢复：`/tmp/ctfbot-c4-recovery-20261007-1/acceptance.json`
- 回执丢失的隔离记录：上述 recovery 目录中的 `lost-acknowledgement/`。这是预期保留的阻断记录；不得伪造 acknowledgement 以清空它。

这些 `/tmp` 文件是本机私有验收材料，可能被系统清理，不作为可分发题目或永久发布归档。全部测试 solver 容器已确认删除，endpoint 已停止。临时受审 profile 保留供审计，授权已短时限定；**未安装项目默认远端 profile**。启用真实目标需要它自己的合法 grant、时段与匹配的审核依据。

## 复跑

```sh
.venv/bin/python doc/phase-c/verify_remote_recovery.py --image sha256:<本机固定镜像ID> --output /private/new-recovery
.venv/bin/python doc/phase-c/verify_remote.py --image sha256:<同一镜像ID> --output /private/new-remote \
  --recovery-acceptance /private/new-recovery/acceptance.json
```

不传 `--recovery-acceptance` 只生成 PARTIAL 记录，不启用 profile。脚本仅为已授权的本机合成验收使用，首次失败留下的目录保留作诊断，复跑应使用新目录。

持续 TCP session、TLS、UDP、多目标、browser、任意容器网络及通用 Compose 不在首个受控 TCP 闭环内；阶段 C 总目标的其他扩展仍按开发计划记录。
