# 阶段 A7：模型 provider 选型记录

状态：已决定暂用 Codex App Server 接入 ChatGPT；它只实现 provider-neutral ModelSession adapter，不作为稳定 provider 承诺。fake JSON-RPC 测试覆盖 tool-result 归一化、usage 收集和文本 delta 去重；一次真实单题 smoke 已通过 controller verifier。真实 usage 仍未返回，错误/timeout/cancel 与跨版本兼容性尚未验收。

## 需要对比的能力

首个 provider 必须支持结构化 tool/function calling、可读取 usage、明确模型 ID、有限输出长度，并提供可区分重试错误/永久错误的状态。Adapter 应把 provider 原始消息转换成稳定内部事件，而不是让 provider SDK 类型进入 orchestrator/evidence schema。

本地已发现 Codex CLI `0.158.0`。临时实现使用官方 `codex app-server --listen stdio://`，支持托管 ChatGPT 登录、模型发现、文本 smoke 和一个固定返回值的 `ctfbot_probe` dynamic tool；不把它伪装成 OpenAI REST API，也不自行读写 Codex OAuth token。纯文本 smoke 收到 `CTFBOT_CONNECTION_OK`；一次实际工具调用返回 `CTFBOT_TOOL_OK`，工具名/调用数正确，未携带题目数据。该次命令因本地把 assistant delta 与完成事件重复拼接而退出 1；收集逻辑已修复，但按用户授权没有重发模型请求。官方文档把 dynamicTools 和 `item/tool/call` 标为 experimental，故结果仅适用于本机 CLI `0.158.0`，不能据此认定生产 adapter 已完成。

后续新增 provider 时按同一契约补充真实兼容评估；当前不为扩展 provider 列表而消耗模型额度：

| 项目 | 候选 | 状态 |
|---|---|---|
| ChatGPT/Codex 登录与模型发现 | Codex App Server stdio adapter | 本机 ChatGPT 登录、模型读取和文本 smoke 已验证 |
| 云端结构化工具调用 | Codex App Server dynamic tools | 固定返回值 smoke 与 cry-babycrypto 一次真实解题工具调用已验证；接口为 experimental |
| 本地模型/兼容 API | OpenAI-compatible server 或其他受控 endpoint | 未发现 endpoint 配置；不假设协议完全兼容 |
| 真实 usage/cost | 经 ModelSession 归一化的 usage 元数据 | fake 事件路径已覆盖；本次真实单题 smoke 的 usage 为 null，价格计算与硬 token/cost cap 尚未实现 |
| 隐私/数据保留 | provider 数据使用条款和用户选择 | 待在使用真实题目材料前确认 |

## 建议的 adapter 契约

- 输入：规范化 messages、有限 tools/schema、模型参数、取消信号、deadline。
- 输出：assistant text、结构化 tool calls（经本地 registry 执行）、usage、provider/model、response ID、finish reason；错误类别可记录，retryability 尚未归一化。
- 只由 controller 进程持有密钥；异常、request transcript 和 `doctor` 输出必须脱敏。
- 结构化调用仍由 ctfbot 本地 schema validator、scope policy 和 budget validator 再校验；模型原生 tool call 不构成授权。
- 用 fake adapter 先验证 orchestrator/event contract；provider 切换不能改变 evidence/event schema。

### 与 Strix 实现的取舍

Strix 的 `models.py` 为 `chatgpt/<model>` 增加了定制 Responses model wrapper，配合 `strix/config/codex.py` 直连 `chatgpt.com/backend-api/codex` 并自行处理 Codex 登录 token。后者明确标注该私有接口在官方产品外不受 OpenAI 支持。ctfbot 使用官方 Codex App Server 管理登录与请求，不镜像私有接口、不导出 token；代价是这层接口仍需随 Codex CLI 版本做兼容验证，暂时不能当作通用 ModelAdapter 已定型。

参考：[Strix models.py](https://github.com/usestrix/strix/blob/main/strix/config/models.py)、[Strix codex.py](https://github.com/usestrix/strix/blob/main/strix/config/codex.py)、[OpenAI Codex App Server](https://developers.openai.com/codex/app-server)。

Codex App Server 是临时 ChatGPT 接入，不进入 agent loop、tool registry、evidence 或 run-event 的 provider-specific schema。新增 provider 通过实现相同 ModelSession 契约接入，先用 fake contract 检查，再在选定时做真实兼容 smoke。单题真实 smoke 已完成；本次一次性传输授权已消耗，任何新模型请求或题目数据传输仍需取得具体授权。价格易变，不在设计文档写死。
