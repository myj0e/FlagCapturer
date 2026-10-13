# Sandbox general-v3 工具补齐与镜像清理

## 来源与变更

根据 `runs/stage-b` 历史记录，显式执行失败包括 objdump、factor、upx、go；`which` 检测未找到 veracrypt、cryptsetup。前两项已由通用工具箱中的 binutils/coreutils 提供，本次纳入明确功能验收。新增 Go（Debian golang-go）、cryptsetup（Debian cryptsetup-bin）、UPX 5.2.0 和 VeraCrypt console 1.26.29。

UPX 使用官方 Linux amd64 发行资产，VeraCrypt 使用官方 Debian 12 amd64 console 包；Dockerfile 固定版本和 SHA-256，安装前校验下载内容。发行元数据、包版本、源文件哈希、构建、功能验收和提升结果保存于 `data/acceptance/20261009-sandbox-v3-*`。安装只发生在构建期。

官方来源：[UPX 5.2.0](https://github.com/upx/upx/releases/tag/v5.2.0)、[VeraCrypt 下载](https://veracrypt.io/en/Downloads.html)。

## Agent 关联修改

crypto、reverse、forensics 的 pack 元数据列出新工具；workflow_environment 返回实际路径、missing_binaries、外部发行版本和运行约束。对应 playbook 说明 UPX 解包先复制到 /work、Go 离线构建使用 /work 缓存、VeraCrypt/cryptsetup 仅执行文件/头部分析及用户态密码操作。YAFU 的样例配置路径与直接传入表达式的方式也已说明。

镜像 inventory 会在任何声明的命令缺失时使构建失败。默认镜像解析要求 general-v3；新镜像验收成功后才提升为 `ctfbot-tools:candidate` 与 `ctfbot-tools:local`。已经启动的容器继续使用原固定 image ID。

运行保持原有非 root、只读根目录、无 capabilities、无额外网络权限；安装磁盘加密工具不授予 mount、FUSE、loop-device 或 device-mapper 权限。

## 清理范围

只删除本项目已核对没有任何容器引用的过时镜像，不使用全局 prune 或强制删除。已删除四个 `ctfbot-stage-a-runtime` 和一个 `ctfbot-c2-session-smoke` 镜像。替代版本验收后，两个旧 general-v2 镜像均已没有容器引用，已一并删除。本次共删除 7 个旧镜像，没有停止任何活动容器。受审 C3 服务 fixture、构建基础镜像和其他项目镜像保留。

## 最终验收

完成修改后统一验收，不运行中途 smoke。真实离线 sandbox 使用自建数据验证：objdump 反汇编、factor 分解、UPX 压缩/检验/解包/执行、Go 离线编译执行、VeraCrypt 密码算法自检、cryptsetup 用户态 LUKS2 头部创建/读取，以及原有工具和运行隔离。随后验收六类 workflow 的环境探测、解题、报告、回放与 holdout，并执行排除唯一 smoke 用例的自动回归。过程不使用真实 LLM 或用户题目。


## 2026-10-09 最终结果

- 当前 image：`sha256:834de6099f67cc05c8402a187f2d4c976d16e5775a062dee3a9e9268a06e9fd0`。
- 已提升标签：`ctfbot-tools:candidate`、`ctfbot-tools:local`；应用默认解析已验证为上述固定 ID。
- 六种历史缺失工具均存在；15 组真实 Docker 功能/隔离验收全部通过。
- 六类 development 6/6 完成候选记录，命令回放全部 matched；独立 holdout 6/6 完成候选记录。
- 自动回归：**283 passed, 1 deselected in 61.40s**；排除项是唯一 smoke 用例。
- 删除 4 个 Stage A、1 个 C2 smoke、2 个旧 general-v2 镜像，均在确认没有容器引用后删除。

私有验收记录：

- `data/acceptance/20261009-sandbox-v3-build-1/build.json`、`inventory.json`。
- `data/acceptance/20261009-sandbox-v3-functional-1/acceptance.json`。
- `data/acceptance/20261009-sandbox-v3-workflows-1/acceptance.json`。
- `data/acceptance/20261009-sandbox-v3-regression.log`。
- `data/acceptance/20261009-sandbox-v3-cleanup/removed.json`。
- `data/acceptance/20261009-sandbox-v3-promotion.json`。

重新启动 TUI 可默认选中 general-v3。已经填入旧固定 ID 的 Runtime 字段需要换成新 ID，或清空后重新预览。
