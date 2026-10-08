# 逐层扩大闭环开发路线文档修改计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 更新两份开发计划，使阶段 A 的实际完成状态准确，并让后续里程碑从首条可用产品闭环逐步扩大到现有完整目标。

**Architecture:** `doc/AI_CTF_AGENT_DETAILED_PLAN.md` 是唯一细粒度路线和验收标准来源；`doc/AI_CTF_AGENT_DESIGN_PLAN.md` 保留调研结论，只同步基线摘要、阶段路线和评测原则。各轮共享安全/可信底线，扩展项按能力触发条件进入后续迭代。

**Tech Stack:** Markdown 文档；仓库现有 `doc/` 结构和相对链接。

**Spec:** [路线调整设计稿](../specs/2026-09-30-iterative-closure-roadmap-design.md)

## Global Constraints

- 每轮不可延期的底线：授权范围、sandbox/网络隔离、资源预算、controller-only oracle、verified 状态准确性和 evidence 可追溯性。
- 真实题目传输和模型额度按题目与预算分别授权；不得把既有单题授权写成批量授权。
- 阶段 A 最小闭环已完成；扩展验收不得继续作为所有后续工作的统一前置条件，也不得将其描述为完整安全认证。
- TUI 是主要产品入口；headless 与 TUI 共用 application service。
- 最终范围沿用完整设计目标，包括六大类基础工作流、报告/复现、受控评测和经审核的通用记忆；先建立明确的六类覆盖矩阵。
- 只修改两份开发计划文档；不修改产品代码，不运行真实题目或模型，不消耗模型额度。
- 不为文档修改新增或运行代码测试；使用 diff、链接和章节一致性检查验证结果。

## Review Focus

- 阶段 A 状态不能回退，也不能把 synthetic runtime smoke 写成完整隔离认证。由 Task 1 对照 `doc/phase-a/README.md` 核实。
- 批量 baseline 和 provider 扩展验收不能重新成为首条产品闭环的前置条件。由 Task 1 检查阶段门槛与遗留项触发条件。
- 为了提前交付而弱化安全、授权、verifier 或 evidence 底线。由 Task 3 核对各阶段 DoD 与统一阻断项。
- 六类领域覆盖不能被“混合/未知类”替代，也不能留下标签歧义。由 Task 1 明确覆盖矩阵归一任务，Task 3 检查最终门槛。
- 上位设计与详细计划不能各自保留冲突的阶段定义或现状描述。由 Task 2 对照 Task 1 的路线摘要，并由 Task 3 复核。

---

### Task 1: 重写详细实施路线与验收门槛

**Files:**
- Modify: `doc/AI_CTF_AGENT_DETAILED_PLAN.md`
- Read: `doc/phase-a/README.md`
- Read: `docs/superpowers/specs/2026-09-30-iterative-closure-roadmap-design.md`

**Interfaces:**
- Consumes: 阶段 A 实际状态表、已确认的完整产品目标和路线设计稿。
- Produces: 唯一权威的 A–D 阶段含义、工作包归属、最低完成门槛和扩展项触发条件，供 Task 2 摘要。

- [x] **Step 1: 校正计划开头的当前基线**，准确写明阶段 A 最小闭环已通过、一次获准 cry-babycrypto smoke 被 verifier 接受、10 道本地 pilot 已准入但未获模型传输授权，以及批量真实 baseline 尚未运行；说明剩余 A 项不阻止阶段 B。
- [x] **Step 2: 重写阶段路线总览和阶段 A**，将 A 定义为已完成基线；用完成状态或“已完成/扩展验收”标记 A 工作包；把 A exit gate 改为最小闭环通过记录，不再要求所有扩展验收先完成。
- [x] **Step 3: 将阶段 B 工作包拆成首条产品闭环所需范围与后续增量**，明确本地附件、最小 TUI 路径、单 agent、core tools、候选验证、evidence 和基础报告；将多领域覆盖、完整 session、广泛兼容性和大规模 benchmark 移到后续阶段。
- [x] **Step 4: 将阶段 C 改为逐种运行能力扩大闭环**，安排生命周期可靠性、交互 session、本地服务和授权远端等增量；要求每种模式在启用前通过对应的边界和失败清理检查。把原独立消融章节改为贯穿 B–D 的评估方式，高成本架构继续要求同预算证据。
- [x] **Step 5: 将阶段 D 定义为领域覆盖和最终目标验收**，按现有设计迭代工具包，明确先将现有领域分组归一为六类覆盖矩阵；纳入混合题回退、公开/改编/私有评测、报告复现和记忆来源/污染治理。
- [x] **Step 6: 同步全局 DoD、工程质量、Epic、风险、阶段预期状态和实施顺序**，把每个里程碑分成范围、进入条件、完成条件、阻断问题及带触发条件的后续完善项；确保原 B/C/D 内容归属与新路线一致。

**Review result:** 详细计划中的每个阶段都能指出“该轮可运行闭环”；Stage A 事实与施工记录一致；安全底线明确且没有扩展项被遗留为隐式统一门槛。

### Task 2: 同步高层设计计划

**Files:**
- Modify: `doc/AI_CTF_AGENT_DESIGN_PLAN.md`
- Read: `doc/AI_CTF_AGENT_DETAILED_PLAN.md`

**Interfaces:**
- Consumes: Task 1 定稿后的阶段名称、路线顺序和最终完成定义。
- Produces: 与详细计划一致的路线摘要；详细工作包仍只在 Task 1 的主文档维护。

- [x] **Step 1: 更新调研文档中的项目现状描述**，把“仓库尚无 README/实现代码”的历史快照改为当前阶段 A 已建立最小 headless 闭环，并链接阶段 A 施工记录。
- [x] **Step 2: 更新结论摘要和建议实现阶段**，说明先完成窄范围的 TUI 产品闭环，再按可靠性/运行模式/领域逐轮扩大；将评测改为贯穿迭代并支撑复杂度选择。
- [x] **Step 3: 同步发布验收原则**，保留按类别报告、验证状态准确、轨迹可复现、授权与安全为阻断项等要求；不新增固定 solve-rate 门槛。

**Review result:** 上位文档能作为详细计划的准确摘要；竞品调研内容不因路线改写而重做或丢失。

### Task 3: 跨文档一致性与差异检查

**Files:**
- Review: `doc/AI_CTF_AGENT_DETAILED_PLAN.md`
- Review: `doc/AI_CTF_AGENT_DESIGN_PLAN.md`
- Compare: `doc/phase-a/README.md`

**Interfaces:**
- Consumes: Task 1 与 Task 2 修改后的两份文档。
- Produces: 完整、互相一致且准确指向当前阶段记录的开发路线文档。

- [x] **Step 1: 检查目标、阶段名和阶段完成定义**，确认上位摘要没有与详细阶段顺序冲突，且“阶段完成”与“最终完成”区分明确。
- [x] **Step 2: 检查安全与授权措辞**，确认越权、未授权传输、secret/oracle 暴露、错误 verified 状态和预算失效仍是阻断问题；批量真实 baseline 需要新的题目与预算授权。
- [x] **Step 3: 检查阶段 A 状态和遗留项排期**，确认基线事实与 `doc/phase-a/README.md` 一致，Docker、provider 和批量评测扩展项按适用能力触发。
- [x] **Step 4: 检查文档差异和内部链接**，运行 `git diff --check -- doc/AI_CTF_AGENT_DETAILED_PLAN.md doc/AI_CTF_AGENT_DESIGN_PLAN.md`，并检查被移动章节的相对链接与 Markdown 标题锚点。

**Review result:** Stage A 状态、完整目标模式、六类矩阵及授权/安全/verifier/evidence 门槛经跨文档对照和独立审阅；审阅提出的阶段 C 验收缺口和里程碑模板缺项已修复。文档级 diff、链接和锚点检查通过；未运行项目代码测试，不启动 Docker，不调用模型，不访问题目数据。
