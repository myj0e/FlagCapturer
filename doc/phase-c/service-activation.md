# C3：受审单服务的启用与恢复

2026-10-07 的第三个增量已将 controller profile 接入默认 TUI 与 `solve`。
首个可启用范围是已经完成实机验收的 authored TCP fixture：固定 service/solver
镜像、固定启动命令、固定端口，以及对应的本机 Docker 节点。

## 启用配置

默认配置位于被 Git 忽略的 `data/service-profile.json`，文件权限为 0600，父目录为
0700，必须由当前 controller 用户持有。它不会挂载进 solver 或 service。

先使用[验收脚本](verify_local_service.py)生成新的私有验收目录。当前脚本输出版本 2
的 `acceptance.json`，包含节点信息、网络结果、7 条生命周期结果及 3 类请求恢复结果。
旧版本或缺项的记录不能用于启用。

```sh
ctfbot service approve \
  --acceptance /absolute/private/acceptance-directory/acceptance.json \
  --basis 'Reviewed authored synthetic fixture and local execution authorization'
ctfbot service status
```

`approve` 固定受审 service/solver 镜像、argv、端口和节点身份，并保存验收文件 hash。
每次预览/启动重新核对验收文件 hash 和其绑定的镜像、命令、端口、节点；请保留该私有
验收文件，迁移时通过 `approve` 重新绑定。节点身份包括 daemon ID、版本、kernel、当前
socket 与本机 boot ID。节点不匹配时必须
重新验收。profile 或恢复记录若放在题目 workspace/runs 内，会在预览时被拒绝。
profile 本身是可信 controller 配置，不是题目可自行提供的授权。

同一配置目录一次只允许一个服务 run 或恢复操作。发现未收尾记录时不能替换 profile
或启动新服务 run。已有本地附件 offline 路径仍由原来的准入和授权流程管理。

可使用另一个私有配置，参数放在子命令之前；不带子命令时进入 TUI：

```sh
ctfbot --service-profile /absolute/private/controller/service-profile.json service status
ctfbot --service-profile /absolute/private/controller/service-profile.json
```

## 运行最小服务闭环

使用与 profile 一致的本地镜像生成新的合成快照。`--authorize-model-data` 仅授权该
authored fixture 的合成输入；profile 不代替逐题模型传输授权。

```sh
image=$(docker image inspect --format '{{.Id}}' ctfbot-c3-fixture:local)
PYTHONPATH=src .venv/bin/python -m ctfbot.challenge.service_fixture \
  --output /absolute/private/new-fixture-directory --image "$image" --authorize-model-data
```

在 TUI 中填写生成的 workspace、外置 oracle、上述 image ID 和另一个私有 runs 目录，
先 Preview，再 Run。预览会显示 profile/授权阻断原因；时间线显示服务状态及 endpoint，
清理失败时显示恢复命令。headless 使用同一 application policy：

```sh
ctfbot solve \
  --workspace /absolute/private/new-fixture-directory/workspace \
  --oracle /absolute/private/new-fixture-directory/oracle.json \
  --runtime-image "$image" --runs-root /absolute/private/service-runs \
  --max-turns 3 --wall-time 60 --confirm-model-usage
```

上述用户运行命令会使用已配置的模型额度。本轮开发验收使用 deterministic model，
没有执行真实 provider 请求。

## Docker 超时与恢复

受审运行在 `data/service-state/<owner>/` 持久记录生成的资源名、ownership 标签、节点
身份和每次 create/start 请求。在发起变更前先落盘请求 intent，再由独立进程等待 Docker
回执；前台截止时间不终止该进程，也不会再次发送创建请求。

前台清理会短暂等待回执并移除当前能确认 ownership 的资源。回执仍未确认时，即使
资源暂时不可见，也保留未收尾状态并阻止新 run。迟到的成功回执可触发自动恢复；也可
在 controller 退出后执行：

```sh
ctfbot service recover
ctfbot service status
```

恢复仅移除已记录且 ownership 匹配的资源，确认删除后才解除阻断。恢复允许在同一
daemon ID/socket 上清理已知资源；新运行仍要求完整节点指纹匹配。回执丢失或 worker
异常退出时，`recover` 可以清理当前存在的资源，但会保留 `pending_or_uncertain`，返回
非零退出码。该状态需要操作者调查 daemon/request 记录；系统不会根据一次“不存在”
检查推断没有迟到请求，也不会自动重试模型或题目。

run evidence 记录 profile hash、验收 hash 与恢复 journal 路径；报告列出 profile hash 和
journal 路径。迟到恢复结果留在
journal 中，不改写已经完成的 run 历史状态。

## 本轮记录和范围

私有验收：`/tmp/ctfbot-c3-activation-20261007-1/acceptance.json`；入口验收：同目录
`activation.json`。固定镜像沿用[第二增量](local-service-acceptance.md)的 image ID。

- 双 run、四个容器的网络隔离检查及 7 条生命周期路径在本轮通过。
- 真实 Docker network create、container create、container start 的延迟回执均通过：
  未确认时阻止新 run，确认后清理资源并解除阻断。
- 实际 `solve` CLI 在受审 profile 下取得 `verified`，镜像、argv、端口四种不匹配被拒绝。
- 当前工作区已安装仅限该合成 fixture 的默认 profile，`service status` 显示节点匹配、
  无未收尾记录。原有 74 项单元/TUI 回归是前一增量的记录，本轮没有重跑该套测试。

TUI 已接入同一 profile policy 和服务状态显示；本轮没有做真实 provider 或交互终端的
服务解题验收。这个闭环不扩展到任意服务镜像、Compose、多服务、恶意镜像认证或每端口
防火墙。后续先推进 C4 显式授权远端 scope，再按需求扩展 C3 的其他服务 profile。
