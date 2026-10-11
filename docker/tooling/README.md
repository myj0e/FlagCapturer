# FlagCapturer Docker 工具箱

此目录是当前项目的构建源，承接 `reference/ctfbot/docker/tooling` 的 general-v3 工具层，扩展为 general-v4。参考项目保持原样。默认 agent 镜像标签仍为 `ctfbot-tools:candidate`。

```bash
docker build --progress=plain -t ctfbot-tools:general-v4 docker/tooling
docker run --rm --pull=never --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --cpus 2 --memory 2g --pids-limit 256 \
  --user "$(id -u):$(id -g)" --env HOME=/tmp \
  --tmpfs "/tmp:rw,nosuid,nodev,size=64m,uid=$(id -u),gid=$(id -g)" \
  --tmpfs "/work:rw,exec,nosuid,nodev,size=1g,uid=$(id -u),gid=$(id -g),mode=0700" \
  --workdir /work ctfbot-tools:general-v4 -c 'python3 /opt/ctfbot/smoke.py && xvfb-run -a python3 /opt/ctfbot/gui_smoke.py'
docker tag ctfbot-tools:general-v4 ctfbot-tools:candidate
```

只在验证完成后更新 candidate；已有运行按不可变镜像 ID 使用原镜像，不受标签更新影响。

基础 Python 库保留在 `requirements.lock`；新增依赖由 `requirements-extra.in` 及带哈希的 `requirements-extra.lock` 管理，编译锁文件时以基础锁为约束：

```bash
uv pip compile docker/tooling/requirements-extra.in \
  --constraint docker/tooling/requirements.lock --python-version 3.12 \
  --python-platform x86_64-manylinux_2_28 --generate-hashes \
  --output-file docker/tooling/requirements-extra.lock
```

独立发行包固定在 `downloads.lock.json`。GitHub 发行包采用上游 release SHA-256，Burp 采用官方 JAR 校验值；NetworkMiner 采用固定版本 HTTPS 下载后记录的 SHA-256，来源在锁文件标注。下载层并行获取并校验，BuildKit 缓存避免失败后重新下载全部文件。基础镜像固定 digest；Debian 软件包取 Bookworm 仓库，实际版本记录在镜像清单中。

## 能力与调用方式

完整清单：`python3 /opt/ctfbot/inventory.py`，或读取 `/opt/ctfbot/inventory.json`。清单包含命令路径、Python 模块、发行包锁、系统包版本和运行边界；缺失声明的命令或模块会使构建失败。

| 领域 | 新增工具 | 调用说明 |
|---|---|---|
| 文本与图像 | Tesseract、eng/chi_sim、OpenCV、pytesseract | `tesseract`；默认 `python3` 可导入 `cv2,pytesseract` |
| 通用/Web | rg、tree、vi、Chromium/Firefox DevTools、grpcurl、Burp | Chromium 使用 headless、`--no-sandbox --disable-dev-shm-usage`，数据目录放 `/work`；grpcurl 使用注册代理允许的网络边界 |
| Pwn/逆向 | pwndbg、Ghidra | `pwndbg --batch ...`；`analyzeHeadless /work project -import /work/file`；保留原 GDB |
| 磁盘/内存/网络 | Sleuth Kit、Autopsy、Volatility 3、Wireshark、NetworkMiner | `fls/mmls/icat`、`autopsy`、`vol`；镜像特定的 Volatility 符号需作为附件提供 |
| 编码链 | CyberChef 离线 Web 应用 | `/opt/ctfbot/cyberchef`；`cyberchef --headless --dump-dom`；未安装 npm Node API |
| 音频/数字信号 | Audacity、PulseView、sigrok-cli、GNU Radio | CLI 优先；GNU Radio 模块使用 `/usr/bin/python3`，不是默认 Python 3.12 |
| Android | JADX、Apktool、adb、Frida | `jadx/apktool` 可离线；设备调试另需已授权的设备/目标 |
| 区块链 | Foundry、solc、web3.py | `forge --offline --use /usr/local/bin/solc`；`cast/anvil`；默认 Python 导入 `web3` |

Java 主目录固定为 `/work`，以支持沙箱中不在镜像 `/etc/passwd` 里的宿主 UID；JVM 设置及缓存落在可写工作区。

GUI 工具已安装，但 agent TUI 没有交互桌面。可用 `xvfb-run` 创建容器内部虚拟显示；Qt 默认 offscreen。不会挂载宿主显示、设备或 Docker socket。Burp 手工功能和首次许可/版本选择需要用户交互；没有声称提供无人值守 Repeater API。NetworkMiner 为免费 GUI 版，包装器将运行副本复制到 `/work/.cache/networkminer` 以保存提取文件，不包含付费 CLI/PcapNG 功能。

搜索引擎、地图/街景、反向图片搜索、网页存档是外部服务，不是镜像内可安装的离线软件。Android 模拟器、真机、SDR/USB 硬件、任意 Solidity 旧版本也不因安装客户端而自动可用；当前镜像固定一个 solc 版本，精确 pragma 不匹配时应在构建期增加对应编译器。容器的网络、权限和设备配置保持现有沙箱约束。
