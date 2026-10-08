# 通用解题镜像 general-v2

`ctfbot-tools` 从最小本机文件导出升级为独立工具箱，源码在 `docker/tooling/`。
Python 3.12 官方 Debian Bookworm 基础镜像固定 digest；与 controller 的 Python
版本独立。构建时可联网下载 Debian 包及哈希锁定的 Python 依赖；解题时不安装依赖。

当前 TUI 默认使用 `ctfbot-tools:candidate`，解析为不可变 image ID；无需重新标记为 `local`。下方提升为 `local` 的命令和验收记录保留历史含义。

## 已包含的能力

| 用途 | 工具和环境 |
|---|---|
| 脚本与基础操作 | Python/pip/venv、Bash、coreutils、grep/sed/awk、jq；Node/npm、Ruby、Perl、Java JRE |
| 整数分解 | YAFU 3.1.9（源码固定到 commit `54c5e8d4c21c8c994421d0c95c16aa6f3bd0280a`，包含 SIQS） |
| Reverse / Pwn | file、strings、xxd、hexdump、binutils、GDB、GCC/G++/make、NASM、patchelf、strace/ltrace、x86 32/64 位运行库、QEMU user |
| 密码与约束 | PyCryptodome、sympy、gmpy2、z3-solver、NumPy、SciPy |
| 二进制 Python | pwntools、capstone、unicorn、pyelftools、pefile、construct、bitstring |
| 图片、取证、归档 | Pillow、ExifTool、binwalk、steghide、foremost、zip/unzip、7z、xz/bzip2/zstd |
| 网络文件分析 | tshark、tcpdump、scapy、dpkt、pyshark；requests、BeautifulSoup、lxml |
| 基础网络命令 | curl、wget、nc、socat、OpenSSL；安装不代表获得网络授权 |

`workflow_environment` 向 agent 返回库是否可用及 tooling profile；完整版本清单位于镜像
`/opt/ctfbot/inventory.json`，包括 Debian 包、Python 包、可执行文件路径。
agent 可通过 `command_run` 调用命令，使用 `script_save` / `script_run` 保存和运行
Python 解题脚本；附件位于只读 `/challenge`，生成文件写入 `/work`。

## 构建、验收、启用

使用新的私有输出目录；候选验收失败时不替换默认镜像。所有脚本使用安装了本项目的 Python 环境。

```sh
.venv/bin/python doc/phase-d/build_tool_image.py \
  --output data/acceptance/<新的构建目录>
# 查看 build.json 中的 image 值，后续只使用该不可变 ID
.venv/bin/python doc/phase-d/verify_tool_image.py \
  --image sha256:<候选镜像ID> --output data/acceptance/<新的工具验收目录>
.venv/bin/python doc/phase-d/verify_workflows.py \
  --image sha256:<候选镜像ID> --output data/acceptance/<新的六类验收目录>
# 审阅上述记录均通过后，标记为本机默认工具箱
docker tag sha256:<候选镜像ID> ctfbot-tools:local
```

TUI 默认仅 inspect 本机 `ctfbot-tools:candidate`，检查 general-v2 标签、无匿名卷和不可变 ID；
不会在启动时自动 pull/build/install。可在 Runtime 字段显式选择其他固定 ID。
原 `ctfbot-c3-fixture` 服务镜像及受审 C3/C4 profile 不随工具镜像升级而变更；
固定服务 profile 仍须使用它验收过的 solver 镜像，不能直接替换为新工具箱。

更新 Python 依赖时显式重锁，并重新构建验收：

```sh
uv pip compile docker/tooling/requirements.in --python-version 3.12 \
  --python-platform x86_64-manylinux_2_28 --index-url https://pypi.org/simple \
  --generate-hashes --output-file docker/tooling/requirements.lock
```

构建记录保存基础 digest、源码哈希和完整包版本；Python 安装使用 `--require-hashes`。
Debian 仓库没有固定到历史快照，重新构建可能取得更新的 Debian 包；不是逐位可复现构建。
每次 run 仍记录实际 image ID。Debian 包的版权文件保留在 `/usr/share/doc`，Python
包保留 distribution metadata；本次为本机使用，未完成对外分发许可证审计。

## 验收范围与限制

2026-10-08：general-v2 已在本机完成构建和验收，提升为 `ctfbot-tools:local`。
实际 Python 为 **3.12.15**，声明的 21 个主要 Python 模块均可导入，11 组功能与
运行边界检查通过。六类 development **6/6 verified**、命令回放 matched，独立
holdout **6/6 verified**。镜像逻辑大小约 2.77 GB；包版本和来源哈希均已记录。

私有持久记录（仓库忽略）：

- 构建、完整 inventory 与提升记录：`data/acceptance/20261008-tools-build-v2-retry1/`。
- 功能验收：`data/acceptance/20261008-tools-functional-v2-2/acceptance.json`。
- 六类解题/报告/回放/holdout：`data/acceptance/20261008-tools-workflows-v2-1/acceptance.json`。
- TUI 自动选镜像、单 ELF 导入、无 oracle 候选、报告与回放：`data/acceptance/20261008-tools-tui-v2-1/acceptance.json`；结果 format_only / verified=false，solver 已清理。

本次最终自动化回归：**153 passed in 31.12s**。所有实机验收仅使用自建数据和确定性
provider，未调用真实模型或运行用户 RSA 附件。

首次构建在导出阶段被中断；重试复用了构建缓存。首轮功能脚本的超时参数超过 runtime
上限，已修正为 120 秒并重跑通过；没有放宽 runtime 限制。失败目录保留供追溯。

`verify_tool_image.py` 在真实 `SupervisedOfflineRuntime` 中检查：所有声明的 Python
库导入、AES/RSA 数论与 SMT、反汇编与 CPU 仿真、32/64 位编译执行、GDB 子进程调试、
strace、pwntools ELF、图片/元数据、PCAP/tshark、归档、脚本语言、pip 依赖、只读挂载与清理。
它使用自建数据，不调用真实模型或私有题目。六类工作流另用既有确定性 provider 验收，
包含报告、命令回放和 holdout；不据此宣称模型真实解题率提高。

运行保持断网、非 root、所有 capabilities 删除、no-new-privileges、只读根目录与资源限制。
GDB 验收仅覆盖启动自己的子进程，不放宽到任意进程 attach。tcpdump/raw packet 等需
额外权限的操作不能因工具存在而启用，PCAP 离线解析不需要这些权限。

YAFU 在构建期从上游固定 commit 编译；运行期间保持离线。中等规模整数可由 YAFU
自动选择 SIQS；更大整数、NFS 配置和运行时资源仍受 sandbox 时限、内存及 CPU 限制。

不含 SageMath、Ghidra、angr、GUI、浏览器或 GPU cracking；需要时作为独立扩展验收。
QEMU 不提供所有目标 sysroot；较新 glibc、特殊架构或专有 runtime 仍可能需要题目专用镜像。
旧的无下载构建器保留为 `export_local_tool_image.py`，默认生成 `ctfbot-tools:legacy-local`，
不覆盖通用镜像。

来源：[Python 官方镜像](https://hub.docker.com/_/python)、
[pwntools 安装说明](https://docs.pwntools.com/en/stable/install.html)、
[Z3 Python 包](https://pypi.org/project/z3-solver/)。
