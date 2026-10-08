# 临时接入：ChatGPT/Codex 登录

状态：本机接入和 provider-neutral dynamic-tool session 已实现；纯文本 smoke、固定返回值 dynamic-tool smoke 和一次经 verifier 确认的 cry-babycrypto 解题 smoke 均成功。此次是 Codex CLI 0.158.0 的阶段 spike，不是已完成的生产 provider 或通用 CTF 解题验收。

## 运行步骤

在 ctfbot 仓库根目录安装项目并启动设置向导：

```sh
python -m pip install -e .
ctfbot llm setup
```

本机须安装 Codex CLI，且 `codex` 命令可从 `PATH` 调用。若尚未安装，按[官方 Codex CLI 文档](https://developers.openai.com/codex/cli)安装。设置向导会：

1. 检查当前 Codex CLI 登录模式。
2. 若不是 ChatGPT 登录，询问是否切换共享的本机 Codex CLI profile；确认后可选择浏览器登录或 device-code 登录。切换可能影响其他使用同一 profile 的 Codex CLI 工具。
3. 从 App Server 获取账号可用模型和 reasoning effort，供用户选择。
4. 将 `provider`、`model` 和 `reasoning_effort` 保存到仓库本地的 `data/llm.toml`。

查看配置和登录状态：

```sh
ctfbot llm status
```

显式请求模型做一次连通性检查：

```sh
ctfbot llm test
```

`test` 会发起真实模型请求，可能占用该 Codex/ChatGPT 账号的可用额度。`setup` 只做登录和模型发现，不运行模型生成。终端菜单也提供 `l` 配置与 `s` 查看状态。

验证一次结构化工具调用（Codex App Server experimental API）：

```sh
ctfbot llm tool-smoke
```

该命令注册 `ctfbot_probe`，工具只返回固定文本，不读取文件或联网；每次执行会发起一个模型 turn。dynamic tools 在本机已完成一次工具往返验证；首次命令因本地重复拼接 delta/completed 回复退出 1，收集代码现已修复。遵守当前模型额度授权，不要为了复核再次运行。

## 凭据与执行边界

- ctfbot 通过子进程启动 `codex app-server --listen stdio://`，使用 Codex CLI 官方管理的 ChatGPT 登录流程、模型目录和 turn API。
- ctfbot 配置文件只保存 provider/model/reasoning effort，不保存 OAuth token、API key、账号邮箱或登录回调信息。`data/` 已在 `.gitignore` 中排除。
- App Server 子进程收到的环境会移除 `OPENAI_API_KEY` 和 `OPENAI_BASE_URL`，避免这条 ChatGPT 登录路径意外切到通用 API key 或自定义兼容 endpoint。
- App Server 以进程级 `mcp_servers={}` 覆盖关闭用户配置的外部 MCP server，避免连接检查意外调用用户已有的 MCP 集成。
- smoke check 使用系统临时目录创建空工作区；请求设置 `approvalPolicy=never`、`readOnly` sandbox 和受限读取目录，结束后尝试删除临时 Codex thread 并清理目录。
- smoke check 的 prompt 要求只返回短文本、不调用工具。该步骤只验证文字生成，不注册 ctfbot 工具，也不处理题目附件。

## 为什么不直接复制 Strix 的 token 接法

Strix `models.py` 的 `CodexResponsesModel` 将模型名映射为 `chatgpt/<model>` 并定制 Responses 请求；配套 `config/codex.py` 直接访问 `https://chatgpt.com/backend-api/codex`，自行获取、保存和刷新登录 token。Strix 源码明确说明该私有 endpoint 在官方产品之外不受支持。ctfbot 使用官方文档公开的 Codex App Server 登录和 stdio JSONL 协议，不复制私有 endpoint 或 token 管理逻辑。

参考：[Strix models.py](https://github.com/usestrix/strix/blob/main/strix/config/models.py)、[Strix codex.py](https://github.com/usestrix/strix/blob/main/strix/config/codex.py)、[OpenAI Codex App Server](https://developers.openai.com/codex/app-server)、[Codex CLI](https://developers.openai.com/codex/cli)。

## 当前限制

- App Server API 会随本机 Codex CLI 版本变化；本机发现版本为 `0.158.0`，其 CLI help 将 app-server 标记为 experimental。本实现尚未完成跨版本兼容验证。
- 实际解题 smoke 采用 gpt-6-luna / max，使用 1 个 turn 与 6 次工具调用后通过 controller verifier；provider 没有返回 usage，不能据此报告 token 或 cost。cost 计算、重试分类、明确的 provider 取消协议与中断后的恢复策略仍未实现或验证。
- App Server 的 dynamic tools 在官方文档中标为 experimental；历史单次往返只证明常量工具协议链路可用，fake JSON-RPC 测试不能替代真实 provider 对 CTF 工具 schema、usage 与错误路径的验收。
- 普通 workspace sandbox 无法直接访问 Docker daemon；解题 smoke 由获准的本机 CLI 在 ctfbot DockerRuntime 的断网 profile 中启动。
- 本机 data/llm.toml 保存 provider/model/effort 配置。题目传输只获准一次且已消耗；模型额外读取了 workspace provenance.json（含审查和哈希元数据、不含答案）。已将可读文件和 runtime mount 限定到 input 子目录，离线 synthetic 单测通过；没有再次调用 provider。没有批量 baseline。
