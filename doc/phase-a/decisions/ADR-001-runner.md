# ADR-001：解题 runner 边界与 EnIGMA 使用方式

- 状态：已决定（阶段 A 范围内）；性能结论留待后续真实评测
- 日期：2026-09-30
- 决策者：ctfbot 项目

## 背景

EnIGMA/SWE-agent v0.7.0 对 CTF 有现成 prompt、类别配置、交互工具和 summarizer。ctfbot 还需统一 provider tool call、scope/budget policy、evidence/event log、verifier 等数据契约。两者的 agent loop 与终端执行协议并不相同。项目决定将 EnIGMA 作为源码静态参考，不投入依赖兼容和运行对照工作，以尽快完成阶段 A 的架构决策。

## 决策

目标架构使用 ctfbot 自有薄 runner，通过稳定的内部 adapter 调用模型和 runtime；不 fork EnIGMA 作为核心。EnIGMA 仅作为源码参考，借鉴交互会话、长输出摘要和按领域配置的设计。阶段 A 不运行 EnIGMA，不建立其解题性能基线，也不将其启动失败作为项目阻塞。

## 原因

1. 授权、安全、预算、证据引用与验证必须由 ctfbot 自己控制，不能依赖 prompt instruction。
2. EnIGMA CTF 模板基于文本 command 交互和预装安全工具环境，与 ctfbot 的原生工具 schema / event / verifier 需适配。
3. 自有薄 runner 已有 TUI/headless 共用的服务边界、策略检查、证据和 verifier 实现；阶段 A 可据此决定项目底座，无需先承担另一套旧版框架的依赖兼容成本。

## 影响与复审条件

- 早期要自行实现可靠的重试、上下文裁剪、预算停止、会话生命周期和轨迹管理。
- 不移植 EnIGMA prompt、答案或题目数据；只取通用交互设计/工具工作流并记录来源。
- 若未来有明确的兼容性或性能问题，再单独立项运行 EnIGMA，并使用相同题目、模型预算与 verifier 做对照；这不影响阶段 A 决策。
- v0.7.0 在本地隔离环境的启动尝试遇到 `swebench` 常量 API 不兼容（5.0.2 缺少 `KEY_INSTANCE_ID`；1.1.0 路径缺少 `MAP_REPO_VERSION_TO_SPECS`）。该结果记录为静态参考的维护风险，不继续修复。
- 本 ADR 是架构边界决策，不是解题效果结论。真实 pilot 评测用于建立 ctfbot 自身基线，不要求 EnIGMA 对照。
