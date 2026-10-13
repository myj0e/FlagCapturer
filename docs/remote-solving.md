# Web 与 Pwn 远端解题

复用 Pi SDK 1.1.0 的同一个 agent/session、自定义工具、取消信号、进度事件、图片和会话续接。解题脚本在原 Docker 镜像运行；容器保持 `network=none`，通过每次运行专用 Unix socket 与受控出口连接目标。无需给容器开放公网，也不下载新浏览器或工具镜像。

## 创建和运行

工作台：

```text
/target https://challenge.example:8443/
/target nc challenge.example 31337
/target socat STDIO,raw,echo=0,opost,onlcr TCP:pwnable.kr:10001
/target 先访问 https://web.example/login，再通过 TCP 连接 pwn.example 的 31337 端口，保持连接，先读菜单再提交 payload
/run
/network
/network add tcp://challenge.example:31337
/network revoke <target-id>
/network off
```

`/target` 创建无附件题目，要求填写题目描述并确认模型数据发送；目标是提示，`/run` 的确认同时明确本次允许连接的初始目标。附件题仍可 `/import`，运行中 `/network add URL` 添加服务。批准一次目标后，范围内的请求不反复弹窗。动态申请与 SSH 指纹确认位于原有底部权限区。

输入可以是 URL、`host:port`、常见 `nc`/`ncat`、`socat STDIO TCP:host:port`、`ssh -p PORT user@host [远端程序]` 命令，或自然语言连接说明。导入时确定性提取明确端点，在现有确认界面展示；多服务和交互顺序保留给 Agent 理解。未注明协议的自然语言端点按 TCP 候选展示，需要在运行确认时核对。无法可靠解析的命令或缺少地址的说明仍可创建题目，运行时补全信息并通过 `network_request` 申请范围。

命令只用于提取连接信息，不在宿主 shell 执行。socat 的 `raw,echo=0,opost,onlcr` 属于本地终端设置，会作为交互提示保留；普通二进制自动化使用已有持久 TCP 工具或 sandbox relay，无需安装 socat。SSH 用户名、PTY 和远端程序作为说明保留，认证继续使用 `credential_ref` 和主机密钥校验。SSH 跳板、复杂 shell 组合等保留为待解释要求，不自动推导初始授权。

UDP、原始 TLS（如 `ncat --ssl`、`openssl s_client`、socat `OPENSSL`）目前只保存连接要求并提示缺少内置传输，不自动变成普通 TCP；HTTPS 仍由现有 Web 工具支持。URL 路径会进入任务与初始/续接提示；查询参数值和 fragment 在生成的任务、提示及工作台展示中隐藏，原始 URL 只保留在本地 manifest 供控制器使用。非秘密查询参数若影响题目行为，可在题目描述中明确说明；密码和密钥应使用凭据引用。

CLI：

```sh
node src/cli.js remote https://challenge.example:8443/ --task '分析服务并获取 Flag' --authorize-model-data --basis '我的 CTF 题目'
node src/cli.js remote 'socat STDIO,raw,echo=0,opost,onlcr TCP:pwnable.kr:10001' --authorize-model-data --basis '我的 CTF 题目'
node src/cli.js solve <workspace> --provider <provider> --model <model> --confirm-model-usage --target https://challenge.example:8443/ --authorize-network
node src/cli.js resume <run-directory> --confirm-model-usage --target tcp://challenge.example:31337 --authorize-network
```

CLI 无交互批准时，未授权目标返回 `approval_required`，不会默认放行。`--network-grant` 继续接受原 v1 TCP/local-service 文件，也支持 v2 多目标文件：保留 challenge_id/task_sha256/input_sha256/basis/expires_at，schema_version=2，targets 为 `{host,port,capabilities,ssh_host_key?}` 数组。能力为 http/browser/tcp/ssh。

`remote` 的连接命令或自然语言说明在 shell 中用引号包成一个参数。`solve --authorize-network` 未提供 `--target` 时使用题目中已解析的初始端点；显式 `--target` 会替换这组初始授权。题目中的其他端点仍可作为提示，但没有当前授权就不能连接。

## Web 解题链路

- `http_session(open)` 创建本题 Cookie 会话；`web_request` 支持 HTTPS、方法、请求头、文本/资产请求体、multipart 表单、文件上传、有限重定向和自签名 TLS 的显式选择。
- 返回 HTTP 状态、脱敏响应头、预览和正文资产。正文保留 Content-Encoding，压缩响应可 materialize 后在沙箱解码；无需把完整文件塞入上下文。
- `browser_open` 使用镜像已有 Chromium 和固定 CDP 适配器。`browser_action` 提供导航、DOM/文本、点击、填表、页面 JavaScript、网络/console 事件、截图和关闭。页面代码不在宿主 Node 执行。
- 登录态分别保存在 HTTP jar 和浏览器 profile。`web_cookie_sync` 显式同步本题 Cookie，不复制 localStorage/页面内存。浏览器下载被禁用，下载走 `web_request`，需要时先同步 Cookie，确保字节进入正式资产链。
- 浏览器依赖不可用时明确返回能力错误；不会自动安装浏览器。原 `http_request` 仍作为 v1 grant 的兼容接口，新解题使用 `web_request`。
- 非标准 HTTP、请求走私、精确重复头等使用获批 TCP relay 加原始脚本；标准 HTTP 客户端会规范化报文，不能代替原始字节路径。

## Pwn 解题链路

`remote_open → remote_read → remote_send → remote_read` 保留同一连接，适用于菜单、泄漏、二阶段 payload。默认不半关闭、不重连、不做 Telnet 协商；常见题目给出的 nc/telnet 地址通常直接使用 raw TCP。真实 Telnet 协商未内置，可在沙箱脚本中通过 relay 使用对应协议客户端。

输出使用 base64/原始 artifact，UTF-8 仅预览。until 读取保留匹配后的剩余字节；返回 offset、EOF、timeout、断线原因。用户可以显式 `remote_shutdown` 或 `remote_close`。每个非空 read 输出成为可恢复的远端根资产。

脚本使用 `network_relay(target_id)` 返回的 loopback 地址：

```python
from pwn import remote
io = remote(TARGET_HOST, TARGET_PORT)
io.sendlineafter(b'> ', b'1')
# 在此连接中重新获取泄漏，再构造和发送当前 payload。
```

HTTP 脚本调用不带 target_id 的 `network_relay` 得到代理，显式设置 requests/curl 的代理选项。工具扩展环境 `tool_exec` 仍离线，联网脚本在主解题沙箱执行。

## SSH 与凭据

用 `--remote-credentials /private/credentials.json` 指定控制器用户拥有、权限 0600 的引用表，运行时不复制到解题目录：

```json
{"challenge":{"username":"ctf","password":"challenge-password"}}
```

也支持 private_key（PEM 文本）和可选 passphrase。模型只传 `credential_ref=challenge`。固定 SSH worker 复用原镜像 Paramiko，与主 solver 和浏览器 worker 分开；不暴露任意本地执行接口。密码/密钥不经过 argv、普通 session_send、证据日志或 Pi 消息。

`ssh_open` 校验显式 SHA256 主机指纹；未配置时工作台展示指纹要求确认，CLI 需要在 grant 中提供 `ssh_host_key`。认证后用 `ssh_exec/ssh_shell` 创建 channel，再 `ssh_read/ssh_send` 或 `ssh_transfer`。二进制默认不用 PTY。`ssh_relay` 把 channel 独占交给沙箱脚本，pwntools 可连接返回的本地端口，无需获得 SSH 密钥；随后不能混用该 channel 的 read/send。

用户已明确远端内部二跳不做强制限制；本地 endpoint ACL 只约束本地拨号，不能约束服务器内部行为。断开 SSH 也不保证远端后台程序退出。

## 动态范围、证据和恢复

授权按本次运行管理，固定批准域名解析出的 IP 集并直接拨号；保留 HTTP Host 和 TLS SNI。新目标/新端口/新 IP 需明确申请或刷新；允许授权 VPN 私网地址。默认有效期八小时，也可指定 expires_at；运行结束立即撤销。撤销/到期关闭已有及建立中的连接。`network_status` 展示获批目标及最近被阻止的请求，帮助识别缺失子域/CDN，避免对失败页面盲目推理。

透明 CONNECT/raw TCP 限制实际目标服务，不承诺同 IP 上严格虚拟主机隔离。代理环境变量只是客户端配置，Docker 断网阻止绕过出口。

远端正文、DOM、截图、TCP/SSH 输出及 SFTP 下载使用 `origin=remote` 根资产，可沿现有资产链 materialize、分析和恢复。凭据、Cookie jar、浏览器 profile 不导出，也不自动恢复。

每十轮后台总结及窗口压缩可生成 recovery_steps：目标、历史有效操作、前提、预期观察、观察证据、脚本/资产引用、易变状态、副作用及失败回退。必须引用实际观察，仍是模型整理，不自动证明解法正确。

Resume 复用 Pi 会话、已有资产和脚本，向 agent 提供恢复指引，主动沿证据支持的路线快速重建进度。旧连接 ID、ASLR 泄漏、fd、菜单和登录状态必须在新现场重新取得；出现偏差退回最近可验证节点继续解题。控制器不机械重放远端写操作，也不额外调用恢复模型。旧记录缺少结构化步骤时提供已有路线、实验记录与原会话作为恢复线索。
