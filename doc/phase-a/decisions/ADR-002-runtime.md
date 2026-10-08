# ADR-002：本地 sandbox/runtime

- 状态：未决
- 日期：2026-09-30

## 候选

1. ctfbot 自有最小 Docker adapter。
2. SWE-ReX 作为 session/runtime 抽象，后端选择 Docker/local 等候选。
3. 经过许可和安全审查的隔离 VM backend（作为攻击面较强题目的备选）。

## 当前观察

Docker CLI/daemon `29.8.1` 存在；普通 workspace 不能访问 daemon socket。经批准在本机以 scratch/静态 BusyBox 做过一次合成 smoke，验证只读 root/input、独立可写 workdir、`--network none`、cgroup CPU/memory/PID、PTY echo 和强制停止清理。没有运行 challenge image，也没有认证恶意镜像逃逸、DNS/redirect、mount 越界或宿主强隔离。SWE-ReX 官方描述有本地、Docker 和远程 runtime，并支持持久命令会话；尚未在本机安装或验证。

## 最低决策门槛

在隔离测试节点实测：只读题目输入、独立可写 workspace、无密钥/no Docker socket、默认断网、目标 scope enforcement、CPU/memory/PID/time/output 限制、PTY/session 输入/输出、异常结束后的子进程/容器/网络/volume 清理。任何一项无实现方案则不接受为默认 backend。

## 决定

暂不冻结默认 backend、不添加 runtime 生产依赖。A3 证明 Docker CLI 适合继续做候选 adapter spike，但完整决策仍需在隔离节点完成上述验证，并按恶意 challenge 风险评估是否需要 disposable VM。普通 Docker 容器不被视为 VM 等级的隔离保障。
