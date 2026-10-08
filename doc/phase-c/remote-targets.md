# C4：受控 TCP 首个增量

2026-10-07 最新状态：首个受控 TCP 路径已通过全部 28 项必需合成验收及额外 HTTP 路径检查；临时受审 profile 下的真实 solve CLI/Textual TUI 也通过。详见 [C4 实机验收](remote-acceptance.md)。项目默认远端 profile 仍未安装；测试 endpoint 已关闭，短时 grant 不授权其他目标。下文保留首、第二增量交付时的记录，历史“尚未验收”不代表最新状态。

## 本轮交付

- `RemoteSpec` 严格校验一个 host、port、TCP 协议、UTC 起止时间和运行授权依据。题目 manifest 中的声明只是请求范围。
- 独立 controller grant 必须是私有文件，位于 workspace/runs 之外，匹配请求范围及不可变 solver 镜像，并提供固定 IP 和 controller 授权依据。模型传输授权继续单独检查。
- 候选 `DockerRemoteRuntime` 继承 offline Docker profile，解题容器使用 `--network=none`。模型只能经 `remote_tcp_exchange` 请求连接；command/session 工具没有网络。controller 不挂载进容器，也不向容器提供连接凭据。
- connector 直接连接 grant 中的数字 IP，检查实际 peer，运行时不解析 hostname、不读取代理变量、不处理 HTTP 重定向。因此 DNS 变化或响应中的 Location 不会自动产生第二个连接。hostname 是授权标签；IP 由操作人员在 grant 中明确固定。
- 每次调用建立一个连接，发送至多 16 KiB，半关闭发送端，读取至 EOF、输出上限或截止时间。最多一个并发连接；调用最长 30 秒，并受 run 预算和授权窗口限制。使用单调时钟补充 UTC 检查，避免系统时间回拨延长已授予的窗口。
- 停止会中断 socket；清理等待连接关闭确认，并确认 solver 容器不再存在。网络状态、授权 hash、预算及请求/响应私有 artifact 接入 evidence，TUI 和报告显示摘要。响应原文不复制到报告。
- `remote_fixture` 可创建私有合成快照及外置 oracle；默认不授权模型传输，也不启动端点或连接。

## 配置契约

远端快照的 provenance 使用 `import_mode: "remote"` 和以下 `remote` 对象。时间与地址仅为结构示例，不是当前有效授权：

```json
{
  "schema_version": 1,
  "host": "fixture.example",
  "port": 31337,
  "protocol": "tcp",
  "not_before": "2026-10-07T00:00:00Z",
  "not_after": "2026-10-07T01:00:00Z",
  "runtime_authorized": true,
  "authorization_basis": "仅限自建合成端点的授权依据"
}
```

controller grant 为版本 1 对象，且只能包含以下字段：

| 字段 | 内容 |
| --- | --- |
| `schema_version` | 整数 `1` |
| `remote` | 与快照完全匹配的上述对象 |
| `pinned_ip` | 单个 canonical unicast IP；字面 IP host 必须与它一致 |
| `solver_image` | 不可变镜像 digest / image ID |
| `authorization_basis` | controller 独立记录的非空授权依据 |

文件权限需为 0600 或更严格，父目录为 0700 或更严格。`grant_sha256` 是读取并验证的 JSON 对象经过排序、紧凑序列化后的 SHA-256，不是源文件字节 hash。生效范围在一次 run 创建时固定；本轮不提供运行中的配置撤销接口。

供后续合成验收使用的显式注入接口：

```python
from ctfbot.application.service import LocalChallengeService
from ctfbot.runtime.remote import CandidateRemoteFactory

service = LocalChallengeService(
    model_factory=deterministic_model_factory,
    model_metadata={"provider": "synthetic"},
    remote_runtime_factory=CandidateRemoteFactory(private_grant_path),
)
```

该接口只供受信任 controller 代码注入，不开放模型工具或 CLI 自动配置路径。合成端点协议是接收 `solve\n` 后返回 `CTFBOT_SYNTHETIC{remote-tcp}` 并关闭。

## 下一增量及启用门槛

1. 以自建隔离端点记录允许连接、错误 host/port/protocol/IP、缺失授权、过期授权、输入/输出限制的结果。
2. 从真实 solver command/session 验证非 allowlist 出网不可达；验证 DNS 变化、重定向响应和环境代理不能产生额外连接。
3. 记录工具超时、run 超时、授权运行中到期、主动停止、连接拒绝、provider 异常和清理异常；确认 partial evidence、连接和容器最终状态。
4. 验证本轮新增的 solver create/start 持久请求、迟到回执、丢失回执及 controller 崩溃恢复实现；代码复用 C3 worker，尚未取得 C4 实机结果。
5. 完成已有 B/C 路径回归、受审 profile 启用流程和真实 TUI/solve 验收后再安装远端 profile。

本轮仅运行 Python 编译及 diff 空白检查，未新增或执行测试、未连接测试端点、未调用真实模型。现有 74 项测试的历史结果不代表 C4 验收。

持续 TCP session、HTTP/TLS 专用语义、UDP、多目标和任意容器网络不在此首个增量内；C4 总验收仍按开发计划推进。


## 第二增量：持久恢复与受审启用接口

本轮代码已交付，尚未执行测试或实机验收：

- `SupervisedOfflineRuntime` 在创建前写入版本 2 recovery state，仅声明一个带 owner label 的 solver 容器，保留 `--network=none`。禁止镜像匿名 volume，并在启动后检查容器运行状态、network mode 和 owner label。
- 每个 create/start 请求先写入私有 intent，再启动 C3 已有的独立 worker。前台超时或取消不抹掉未确认请求；同配置目录下未完成记录阻止下一次 admission。
- 收尾仅删除 owner 匹配的资源。未收到 Docker 回执时，即使容器当前不存在也不清除记录。controller 崩溃后，`remote recover` 核对原 Docker daemon，处理已拥有的容器；回执未知仍保持阻断，不自动重试解题。
- socket 关闭未确认时，恢复记录保持未完成。controller 仍存活时外部恢复不推断 socket 已关闭；记录 PID、Linux process start ticks 和 boot ID，以区分退出/重启及 PID 复用。进程退出后其 socket 由操作系统关闭。
- `ReviewedRemoteFactory` 每次运行重新校验私有 profile、grant、完整验收 case 状态、验收文件 hash、不可变镜像及当前 Docker identity。grant、镜像、节点或授权时段变化需要重新审查。

### 管理命令

```sh
ctfbot remote status
ctfbot remote recover
# 仅在取得并人工审阅完整 C4 验收记录后执行：
ctfbot remote approve --acceptance /private/acceptance.json --basis '完整验收的审查依据'
```

默认 profile 是 `data/remote-profile.json`，grant 是同目录的 `remote-grant.json`，状态目录是 `remote-state/`。profile 与 grant 必须是同一私有目录里的不同文件，且不能位于题目 workspace 或 runs 内。可在子命令前指定：

```sh
ctfbot --remote-profile /private/controller/remote-profile.json --remote-grant /private/controller/remote-grant.json remote status
```

同一组全局参数也传递给 TUI 和 `solve`。`recover` 不要求 grant 存在或仍在有效期内，因此授权过期后仍可收尾。当前没有运行 approve、没有创建默认远端 profile，也没有增加模型额度使用。

### 验收记录契约

版本 1 私有 JSON receipt 包含：`schema_version: 1`、`result: "PASS"`、`network_profile: "offline-remote-tcp-v1"`、`daemon`（当前 daemon identity）、`solver_image`、`grant_sha256` 和 `cases`。

`cases` 必须完整包含代码 [REQUIRED_CASES](../../src/ctfbot/application/remote_profile.py) 所列的范围拒绝、无网络容器、DNS/redirect/proxy、预算/停止、清理失败、create/start 迟到回执、controller 崩溃、未确认回执阻断、B/C 回归及入口验收。每项需有对应 `status`、`resources_removed: true` 及非空 `evidence` 位置；被阻断的恢复用例即使资源已移除，也必须保留未完成 intent。验收记录和 profile 都是受信任 controller 操作资料；检查格式与 hash 不能替代人工审阅 evidence。

下一步仍是隔离端点和真实 Docker 的上述验收，而非直接开放任意远端目标。Python 编译及 diff 空白检查不能替代该验收。
