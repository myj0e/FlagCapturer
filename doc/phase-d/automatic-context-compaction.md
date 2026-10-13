# Agent 上下文自动压缩

## 行为

当前窗口占用严格超过 80% 时，控制器自动生成不超过 8 KiB 的结构化状态快照，并替换模型会话；随后携带原有安全约束、任务引用和快照继续同一运行。不会因 token 使用量终止任务，也不会调用模型生成摘要或调用 provider 的压缩 API。

摘要、观察、候选 flag、失败实验、脚本、后台会话、检查点和原始证据仍由当前控制器持有。快照只选择近期记录，遗漏部分通过 `state_read` / `artifact_read` 获取；超大元数据原文写入 artifact，不直接丢弃。原始任务全文也保留为 artifact，新会话被要求先读取它再判断剩余任务范围。模型报告的摘要始终不是控制器证明。

工具副作用只执行一次：压缩在同步会话检查点、下一次工具执行前或下一轮开始前触发；已执行的结果已写入证据，正在等待调度的下一次调用不会执行。重建仅影响模型会话，不重建 sandbox 或工具 registry；真实后台任务继续运行。用户停止优先于压缩。

## 分层

- `agent/context_policy.py`：`ContextPolicy` 配置和线程安全的 `ContextPressure` 测量/阈值判断，无 provider、模型名、SDK 或 API 依赖。
- `agent/context.py`：控制器状态索引和有界快照；原始数据保留在所属模块及 evidence 中。
- `agent/context_events.py`：标准用量事件进入测量器，账单累计统计保持独立。
- `agent/loop.py`：在安全边界生成快照、请求会话替换并恢复任务。
- `model_adapters/protocol.py`：可选 `ResettableModelSession` 契约与 `ContextCompactionRequired` 协作让出信号。新 provider 实现普通会话重建即可接入，不需要支持特定压缩接口。

Codex 适配器以关闭旧 App Server、正常启动新 App Server / thread 实现会话替换。使用普通启动能力，避免继承旧会话的待处理调用或通知。同步事件检查点允许长 provider turn 内及时让出；工具处理层必须原样传播让出信号，不能将其伪装为工具失败。后台事件回调只记录测量，不在后台线程执行压缩。

## 测量与边界

占用依次采用：明确报告的当前上下文 token 数、最近一次推理的输入 + 输出（估计）、本地可见文本 UTF-8 字节估计。后者按每字节一个 token 保守估算，不声称与特定 tokenizer 精确相等。新的标准测量会替换本地估计。累计账单 token 不能作为窗口占用。

容量由标准用量事件提供，也可通过 `AgentLoop(context_policy=ContextPolicy(capacity=...))` 显式传入，显式配置优先。容量未知时不猜测型号窗口，继续运行并等待容量信息。估算不包含未向控制器公开的 provider 内部内容，不能保证在尚未收到用量事件的长推理中提前让出。

压缩后的 statusline 立即显示新的估计占用，并保留跨会话累计 token 用量，K/M 单位不变。若新会话固定指令、工具 schema 和恢复快照本身已超过阈值，记录 `seed_exceeds_threshold`，不会无进展地连续重建；待观测低于阈值后再重新启用触发。未实现重建能力的适配器仅记录一次 `context_compaction_unavailable`，继续任务，不伪称已经压缩。会话重建失败仍作为实际 provider 错误报告。

## 最终验收

开发中未运行 smoke 或中途测试。完成实现后统一执行离线验收，排除仓库唯一 smoke 用例：

```sh
.venv/bin/python -m pytest -q --deselect tests/test_stage_a_runner.py::OfflineAcceptanceTests::test_synthetic_agent_loop_completes_without_provider_or_docker
```

新增验收覆盖严格 80% 边界、输入/输出估算、未知容量、超大恢复种子防重建循环、长 turn 内检查点及工具边界让出、跨会话状态与原始任务恢复、工具不重复执行、账单连续、重复压缩、手动取消、快照大小、适配器普通启动重建以及让出信号不被吞掉。验收不访问真实 LLM / Docker。

最终结果：**282 passed, 1 deselected（58.00s）**。唯一排除项为上述 smoke 用例；新增自动压缩验收全部通过。
