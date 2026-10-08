# ADR-003：首个 LLM provider

- 状态：已决定临时接入；provider-neutral 边界已定，真实 provider 验证后续进行
- 日期：2026-09-30

## 决策约束

当前临时 provider 使用本机 Codex App Server 的 ChatGPT 登录。内部 `ModelSession` 契约不暴露 Codex 类型；provider 切换不得更改 agent loop、tool registry、run event 或 evidence schema。稳定 provider 的 tool calling、usage、超时/取消、错误分类和模型 revision 要在后续 provider 真实验证时确认。

## 当前环境与方案

本机发现 Codex CLI `0.158.0`，配置模型为 `gpt-6-luna` / `max`。ctfbot 通过 Codex App Server 检查 ChatGPT 登录状态、模型清单，完成一次纯文本 smoke，并获授权进行一次只返回常量的 `ctfbot_probe` 工具调用；工具返回 `CTFBOT_TOOL_OK`。请求没有携带题目数据。首次 smoke 命令因将文本 delta 和完成事件重复拼接退出 1，汇总代码已修正但没有再次调用模型，故缺少修正后端到端命令通过证据和该 turn 的 usage 归档。

Strix 的实现将 `chatgpt/<model>` 映射到私有 `/backend-api/codex` endpoint 并自行管理 token；Strix 源文件说明此 endpoint 在官方产品之外不受支持。本 ADR 不采用该路径，改走官方 Codex App Server。参考：[Strix model wrapper](https://github.com/usestrix/strix/blob/main/strix/config/models.py)、[Strix token/backend endpoint](https://github.com/usestrix/strix/blob/main/strix/config/codex.py)、[Codex App Server 文档](https://developers.openai.com/codex/app-server)。

## 决定和限制

- `ctfbot llm setup/status/test` 是临时配置和可达性入口；`test` 与 `tool-smoke` 只有用户主动运行才发起模型请求。ChatGPT/Codex 是当前临时接入，不代表最终 provider 选型。
- 本机已经使用 ChatGPT 登录状态并完成一次工具往返，但只在 Codex CLI `0.158.0` 验证；官方 App Server 将 dynamic tools 标为 experimental，版本兼容面未知。
- `ModelSession` 已把 provider-specific 请求/事件转换封装在 adapter 内；FakeModelSession 可在不调用模型的情况下验证 orchestration 契约。Codex adapter 保留原始 usage 字段，但历史 smoke 没有 usage 归档；cost cap、retry taxonomy、取消语义和多 provider 兼容尚未验证。
- 新增 provider 时实现相同协议，不改 agent/tool/evidence schema；届时再做真实兼容测试。当前不发起新模型请求，也不把题目传给 ChatGPT；真实 CTF smoke 需对具体题目和数据传输范围单独取得确认。
