# 阶段 A4：CTFTiny Compose 配置审查

状态：10 个 Compose 候选完成只读静态配置审查；没有拉取镜像或启动容器。10 个均判定为 **不可按上游配置直接运行**。

来源 commit：`f1c9531672c45b24b7fb5f3aa44a7ac33d3602f8`。审计 JSON：[compose-audit.json](compose-audit.json)；复现脚本：[audit_compose.py](spikes/audit_compose.py)。脚本用 PyYAML `safe_load`，只输出 Compose 的服务、镜像、挂载、端口、网络和风险标志，不输出环境变量值。

## 发现

- 10 个 Compose 配置都加入名为 `ctfnet` 的外部网络；它不是本 run 创建的隔离网络，因此可能连接同网络里的其他容器。10 个配置都使用至少一个可变镜像引用。
- `cry-super-curve`、`cry-the-lengths-we-extend-ourselves`、`pwn-puffin`、`rev-maze`、`web-poem-collection`、`msc-showdown` 挂载 `/var/run/docker.sock`。容器可通过 daemon socket 控制宿主 Docker；`msc-showdown` 另外设置 `privileged: true`。这 6 项按原配置排除，不运行。
- `cry-super-curve`、`cry-the-lengths-we-extend-ourselves`、`pwn-puffin`、`pwn-bigboy`、`pwn-roppity`、`web-shreeramquest` 发布了未绑定 loopback 的宿主端口；这些需要改成仅本机访问，或完全不发布端口并让 agent 连接 per-run network alias。
- build context、Dockerfile、入口程序和启动脚本仍需人工审查；仅修改 Compose network/port 不足以完成准入。
- `web-shreeramquest` 同时使用本地 bridge 与外部 `ctfnet`，数据库变量仅记录键名，不在审计报告保留值。

## 运行准入规则

后续若要保留无 Docker socket 的服务，应生成单次 run 专属 Compose 配置：创建仅本题使用的 `internal: true` network；只允许必要的 agent/target 互通；宿主端口默认不发布，必要时绑定 `127.0.0.1`；清除 host mounts、privileged、额外 capabilities、host network/device access；固定 image digest 和资源限制。不要复用外部 `ctfnet`，也不要把 agent、target、oracle 或其他 run 放在共享网络中。

即使完成配置转换，也要先审查镜像来源、digest、Dockerfile 和服务 entrypoint，之后才运行本地环境；绝不访问 `challenge.json` 中声明的远端箱子或外部题目目标。这个静态审查不构成容器安全认证。
