# 运行配置

配置文件、附件和产物保存在私有目录，默认 data/、runs/、reports/ 均忽略提交。Pi 的模型与认证配置仍由 Pi 管理。`CTFBOT_PROVIDER`、`CTFBOT_MODEL` 可作为 CLI 默认值；`FLAGCAPTURER_PI_SDK` 指向可信的 SDK 构建入口，不是模型 URL。

工作台通过 `/login`、`/model`、`/thinking` 配置服务；自定义 API 使用 `/endpoint`。非敏感选择与端点定义保存于 `data/settings/`（可用 `CTFBOT_SETTINGS_DIR` 覆盖），认证复用 Pi 存储，详见 [交互工作台](workbench.md)。

## 新网络 grant

离线 workspace 默认没有网络工具。显式传 `--network-grant data/network-grant.json` 才会注册 broker。题目描述、模型输入、playbook 无法开启网络。示例（须填入 preview 的实际值，示例 IP 为文档保留地址）：

```json
{
  "schema_version": 1,
  "kind": "tcp",
  "challenge_id": "<manifest.id>",
  "task_sha256": "<manifest.task_sha256>",
  "input_sha256": ["<each manifest.files[].sha256>"],
  "basis": "独立审阅后允许访问的比赛目标与授权依据",
  "expires_at": "<future ISO UTC time>",
  "ip": "192.0.2.1",
  "port": 31337
}
```

只接受 literal IPv4/IPv6，不做 DNS；每次最多 16 KiB 输入/输出，30 秒或剩余授权时间的较小值，结束发送后半关闭写端等待 EOF。`http_request` 只生成 GET/HEAD，明文 HTTP，不跟随重定向、不自动解释 TLS/代理。授权在每次调用与连接建立时检查，到期终止进行中的远端请求。网络错误作为工具失败记录，不是无内容成功。

本地服务使用同一授权绑定，`kind` 改为 `local-service`，删除 `ip`，增加：

```json
{
  "service": {
    "image": "sha256:<64 lowercase hex digits>",
    "argv": ["python3", "/opt/ctf/service.py"]
  }
}
```

服务镜像需包含 Python3 和已审阅服务程序；端口为该服务在其容器内监听的 TCP 端口。服务在独立 network=none 容器中运行，不发布宿主端口；broker 经 docker exec 访问容器 loopback，solver 仍断网。grant 到期自动关闭 broker、取消进行中的交换并清理服务容器，solver 可继续离线工作；服务清理失败会使当前 run 失败并保留恢复凭据。本地 service 采用相同 30 分钟进程监督保护；如需更长服务，应先设计新的独立生命周期契约。不会自动沿用旧 C3/C4 profile、grant、acceptance.json。工具镜像和服务镜像都必须事先安装在本机。

## 受审记忆快照

显式传 `--memory data/reviewed-memory.json`：

```json
{
  "schema_version": 1,
  "reviewed": true,
  "basis": "审阅依据",
  "namespaces": ["general"],
  "entries": [{
    "id": "technique-1",
    "namespace": "general",
    "body": "已审阅的通用技巧文本",
    "body_sha256": "<SHA256 of UTF-8 body>",
    "source_url": "https://example.com/source",
    "license": "<actual license>",
    "tags": ["crypto"],
    "contamination": "general"
  }]
}
```

每条 body 必须匹配哈希、属于显式 namespace，并提供许可。允许 general/development，拒绝 holdout。只暴露该次装载的内存快照；没有网络搜索、自动记忆写入和跨任务隐式共享。

## 工具开发契约

工具在 src/tools 下声明参数 schema 和 handler，通过 ToolRegistry 调用，Pi adapter 不包含业务分支。schema 校验拒绝多余字段、错误类型、超长字符串和 NUL；额外边界校验路径、ID、哈希和授权。共享状态操作顺序执行，所有完整返回先保存证据再生成最多 16 KiB 的模型文本。图片另以真正 image block 提供。

单题业务状态不随 Pi compaction 丢弃。summaries、claims、experiments 的结论是模型报告，source_ref 仅检验字节和参数一致性，不证明脚本使用该参数或结论正确。证据 JSON 也是不可信数据；终端渲染必须经 safeTerminal。
