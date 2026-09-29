# AI + CTF 竞品调研报告

调研日期：2026-09-29  
范围：公开 GitHub 项目、官方项目文档及相关研究基准  
用途：为 ctfbot 选择技术起点、明确差异化方向和避免重复建设提供依据

## 1. 执行摘要

当前 AI + CTF 项目大致分为三类：

1. **解题 agent 框架**：EnIGMA、D-CIPHER、CTFAgent 等关注模型如何判断、调用工具并迭代。
2. **解题工作台/编排平台**：NUSGreyhats 的 ctf-agent-workstation 用 Claude Code/Codex 等现成 agent，提供题目管理、工具环境、并行运行、平台集成和 Web/Discord 操作。
3. **评测与训练项目**：NYU CTF Bench、Cybench、CTFTiny、CTFJudge、Cyber-Zero 主要解决“如何评测/训练 agent”，不等同于开箱即用的解题产品。

对 ctfbot 的核心结论：**“为领域配工具 + 多 agent 分工”本身已经不是明显差异点。** EnIGMA 已经实现 CTF 定制接口和交互式工具；D-CIPHER 和 ctfagent 已经采用多角色结构；NUS 工作台则把领域 skills、专业工具链、多个 agent、持久 workspace、竞赛平台接入和并行赛马组合起来。ctfbot 若要形成差异，应聚焦可验证的解题过程、低成本且模型无关的工具接口、可复用的题目/轨迹格式和可靠的基准评测。

没有一个项目可以不经评估就作为 ctfbot 的完整底座。优先评估建议：

- 若要研究通用 CTF agent：先跑 EnIGMA 做基线，重点审查其 SWE-agent v0.7.0 兼容约束和改造成本。
- 若要快速交付团队用的 CTF 工作台：重点评估 NUSGreyhats/ctf-agent-workstation；它是目前与“管理题目并调用现有 agent 解题”最接近的公开参考，但其目标是云端工作站，且使用 GPL-3.0。
- 若要做轻量本地 TUI：参考 AI-CTFer 的 `challenge.yml`、Docker、模型适配和每题输出目录，借鉴其单题流程；ctfbot 可在此基础上增加实时运行监看、证据浏览和交互会话。它适合作为流程样例，不能仅凭 README 当作成熟、全面的解题引擎。
- 多 agent、历史 writeup RAG 和大量定制工具先不要作为首版前提，先用固定预算做单 agent 对照实验。

## 2. 项目分类与对比

### 2.1 直接解题系统与工作流

| 项目 | 定位及架构 | 可借鉴之处 | 局限、成本与 ctfbot 的关系 |
|---|---|---|---|
| [EnIGMA / SWE-agent](https://github.com/SWE-agent/SWE-agent) | 研究型通用 CTF agent。将 SWE-agent 的 ACI 扩展到 CTF，加入可保持 GDB、netcat 等交互会话的 IAT，以及长输出 summarizer。其官方文档报告在 NYU CTF Bench 200 题上解出 13.5%，并注明 CTF 模式目前只兼容 SWE-agent v0.7.0。SWE-agent 当前代码库仍活跃，但 EnIGMA 与当前版本之间的兼容性需要单独确认。 | 最直接回答“是否要为模型重做工具”：应改进模型与工具之间的命令、反馈、交互会话和长输出处理；不是把每个底层工具都重写。MIT 许可，作为原型基线较方便。 | 最接近 ctfbot 的 agent runtime，但旧版绑定可能带来 provider、依赖和维护负担。研究结果受模型与预算影响，13.5% 也说明不能指望换工具接口就解决 CTF 的推理难题。适合 PoC，不应未做适配试验就整仓 fork。 |
| [NYU `nyuctf_agents` / D-CIPHER](https://github.com/NYU-LLM-CTF/nyuctf_agents) | 研究框架，包含 baseline 和 planner → executor 的多 agent 流程，并可选 auto-prompter；使用 Docker 运行题目。仓库也提供单 executor 入口。论文报告的特定配置在 NYU CTF Bench、Cybench、HackTheBox 分别达到 22.0%、22.5%、44.0%。 | 角色分工有研究价值；提供单 executor 对照入口，适合参考如何做架构消融。NYU 同时提供 200 题测试集和 55 题 development split，方便建立可复现实验。 | 多 agent 增加模型调用、交接和状态同步成本；各基准结果不是同条件比较。它更像研究代码而非可直接作为多供应商产品的通用引擎。MIT 许可。 |
| [NUSGreyhats/ctf-agent-workstation](https://github.com/NUSGreyhats/ctf-agent-workstation) | CTF 团队工作台，而非自研底层求解模型。使用 Claude Code/Codex 的原生集成，提供 Web UI、Discord、题目和 workspace 持久化、技能目录、单 agent/并行赛马、用户中途 steering、运行统计、CTFd/rCTF/HTB 等平台插件，以及云端 disposable worker/VPN。仓库有独立 DESIGN、SWARM 文档；研究快照显示 273 次提交。 | 与 ctfbot 原始构想重合度最高。特别值得学习的是每个 agent 独立运行目录、经过验证后共享突破、技能目录运行时加载、暂停/恢复、成本统计、赛题平台接入。它表明可先整合现成 coding agent 和技能，而非自建所有模型调用层。 | 项目重点是工作站和赛事操作，不是模型无关的 CTF 求解器；当前主要依赖 Claude/Codex 运行时。它使用完整云 VM/广泛工具链，运维与部署面较大；文档强调丰富且宽松的执行环境，产品化部署需独立审查执行隔离和凭据边界。GPL-3.0，复用前需评估许可证义务。若 ctfbot 面向个人本地 TUI 或可替换模型的 agent，不应照搬整个平台。 |
| [AI-CTFer](https://github.com/Archerui/ai_ctfer) | 轻量单题 CLI：每道题使用 `challenge.yml`，配置类别、描述、端点、限制和模型；Docker 中运行命令；支持 OpenAI/DeepSeek provider 配置，保存 plan、notes、writeup 和 solve 脚本。README 提供 fake-LLM smoke 命令。 | 题目 manifest、每题隔离目录、模型配置和复现产物都很清楚；适合参考 ctfbot 的题目 manifest、单题流程和产物管理。MIT 许可。 | README 仍描述网络可用的 Docker sandbox，scope 控制需进一步验证；其目标偏单题工具调用 CLI，并非研究型多类别架构。GitHub 页面在调研快照显示提交历史较短、没有 release 信息，因此宜视作有价值的实现样例而非已验证的平台底座。 |
| [humaidhahm/ctfagent](https://github.com/humaidhahm/ctfagent) | 多领域 supervisor + 专家 agents，README 描述 28+ 工具、netcat session、experience DB、类别 RAG、CLI/API 和可选记忆服务。 | 覆盖了 ctfbot 提到的“领域 agent、工具集、历史经验”组合，是检验这套思路是否已经有人实践的直接参照。 | 功能面较宽，运行依赖也更重：Docker 或 Linux/WSL、多个模型供应商；启用记忆服务还需要 Postgres/Bun 和外部 writeup/代码语料，原生安装可能需要 sudo 安装系统工具。RAG 检索解题 writeup 对真实练习有帮助，但会污染盲测。公开 README 里的特性描述不等同于独立基准结果。 |
| [CTF Cyber Agent](https://github.com/allen-haitao/ctf-agent) | 以 observe → hypothesize → plan → execute → collect evidence → verify 为中心的证据驱动 CLI；保存结构化事实、假设、实验和 append-only 证据，要求显式授权并限制目标主机。README 标为 v1.0.0，同时明确表示 reverse、crypto、forensics 尚属计划范围。 | 最值得借鉴的是事实/推断分离、失败实验记忆、范围约束和可审计报告。它把“ agent 正在做什么”转换为可检查的实验记录。 | 当前能力重心为 web 与 pwn，不能视为覆盖全部 CTF 类别的竞品。适合作为状态与安全设计参考，而非全功能 fork 底座。MIT 许可。 |
| [Claude Code CTF-Agent workflow](https://github.com/nonoleekr/CTF-Agent) | 基于 Claude Code 原生文件、shell、编辑和 web 能力，通过类别 playbook、triage 脚本、MCP 特殊工具及每题工作目录解题；不另建 LLM orchestration。 | 验证了“skills + 原生命令行 + 少量真正缺失的专业接口”这条低成本路线。若 ctfbot 首版服务于单一 coding agent，可显著减少重复造工具。 | 绑定 Claude Code 的运行能力和权限模型；迁移到其他模型或统一 benchmark runner 时需要额外适配。公开 README 描述工作流，但没有与 EnIGMA/D-CIPHER 同类的统一公开基准对比。 |

### 2.2 重要的底层构件

| 项目 | 解决的问题 | 对 ctfbot 的价值 | 不解决的问题 |
|---|---|---|---|
| [SWE-ReX](https://github.com/SWE-agent/SWE-ReX) | 在本地容器或云执行环境中运行命令，提供持久 shell session，能与 IPython/GDB 等交互，并支持多 session 与并行执行。MIT 许可。 | 可作为隔离执行/会话运行时候选，帮助把 agent 策略与 Docker/云基础设施拆开；比从零维护终端 session 更省力。 | 不提供 CTF 分类、领域策略、flag 校验或题目生命周期；执行 sandbox 仍需按 ctfbot 目标定义网络、文件和资源权限。 |
| [CTFJudge](https://github.com/NYU-LLM-CTF/CTFJudge) | 用多个 LLM agent 把 writeup 拆成步骤、提取轨迹动作，再比较 writeup 和实际 trajectory 做定性评分。 | 可参考轨迹复盘和“过程质量”评测，补充仅用 flag exact match 的评测盲区。 | 依赖 writeup 和对应轨迹格式；LLM judge 本身不是可信 flag oracle，不能替代确定性校验。仓库快照显示提交有限，应先验证评分稳定性。 |

### 2.3 研究与基准项目（不是可直接替代 ctfbot 的产品）

| 项目 | 核心内容 | 适合用作什么 |
|---|---|---|
| [NYU CTF Bench](https://github.com/NYU-LLM-CTF/NYU_CTF_Bench) | 200 道公开挑战，6 个类别；另有 55 题 development 集；以 Docker 环境组织。 | 主评测候选。迭代时用 development，保留 test 集；报告类别结果并标记公开题污染。 |
| [Cybench](https://cybench.github.io/) | 40 道来自 4 场赛事的专业级任务，带分阶段 subtasks。 | 测试更复杂挑战与阶段性进展；题数少，结果要报告多次运行和波动。 |
| [InterCode](https://github.com/princeton-nlp/intercode) | 100 道 picoCTF 交互式任务，提供执行反馈。 | 快速验证基础工具循环；不能单独代表现代跨类别 CTF 能力。 |
| [CTFTiny](https://github.com/NYU-LLM-CTF/CTFTiny) | NYU 的 50 题轻量基准；配套 CTFJudge 研究调参和轨迹判断。 | 降低评测迭代成本；仍需要私有/改编题检验公开基准上的答案记忆。 |
| [Cyber-Zero / EnIGMA+](https://github.com/amazon-science/cyber-zero) | 使用公开 writeup 合成解题轨迹以训练模型；发布适配 EnIGMA+ 的 benchmark 修复集，并报告最高 13.1 个百分点提升。 | 后期研究轨迹合成、批量评测和训练数据质量；不应作为首版在线解题核心。 |
| [CTFAgent 论文](https://arxiv.org/abs/2506.17644) | 研究 CTF 技术知识评测、两阶段 RAG 和环境交互增强；论文报告特定 InterCode 实验从 39/100 提升到 73/100。 | 说明工具环境反馈和技术知识都可能重要。论文称代码访问受审查，故列为研究参照而非可直接 fork 的 GitHub 产品。 |

**成绩解读限制：** 上述数字来自不同论文或项目自身报告，模型、token/轮次预算、任务子集、环境镜像、提示、运行次数都不一致。它们不能组成一个统一排行榜。EnIGMA 等论文还记录了公开题目和模型预训练数据重叠可能导致的“复述题目文件”现象，因此公开 benchmark 高分不能单独证明泛化能力。

## 3. 竞品优势与未解决问题

### 3.1 已形成的竞争基线

- **模型友好工具接口已经被验证为重要设计。** EnIGMA 的 ACI/IAT 和 SWE-ReX 持久交互终端表明，工具调用、会话管理、输出长度不是无关紧要的实现细节。
- **领域 playbook 比堆砌工具名更可复用。** NUS 工作台和 Claude Code workflow 将解题经验写成版本化 skills；NUS 还支持题目级和 run 级选择、运行中更新和暂停恢复。
- **团队协作和人工 steering 已有成熟参考。** NUS 工作台提供 agent 赛马、独立目录、共享已验证突破、顾问 agent、Web/Discord 控制、平台导入与 flag 提交；这些是 ctfbot 若面向比赛团队需要考虑的产品能力。
- **证据化解题正在成为显式设计方向。** CTF Cyber Agent 的实验记录和 NUS 的 per-run notes/logs 表明，不能只依赖聊天历史来恢复长期题目状态。
- **有大量可用基准，不必先自建大数据集。** NYU、Cybench、InterCode、CTFTiny 可构成由开发集到难题集的评测梯度；关键缺口是环境可靠性和防答案污染，而非缺少公开题目。

### 3.2 仍普遍存在的缺口

1. **跨项目接口不统一。** 题目描述、附件布局、Docker 启动/flag verifier、工具结果和轨迹各有格式，迁移工具与比较结果成本高。
2. **很多项目的重点不同于 ctfbot 的目标。** EnIGMA/D-CIPHER 偏研究和基准；NUS 偏工作台和竞赛运维；一些轻量项目偏单题 CLI。没有一个现成方案同时兼顾模型可替换、深度 CTF tool ACI、轻量部署、开放评测和多赛事接入。
3. **公开基准存在污染和记忆题解的风险。** 仅凭 flag 格式命中可能误判；writeup RAG 能提高真实解题效率，也会让 benchmark 失去衡量新题能力的意义。
4. **多 agent 不保证更高性价比。** D-CIPHER 和工作台赛马证明了多 agent 是可实现的，但每条路线都消耗模型额度，重复侦察、上下文压缩、结论冲突和并发资源需要协调。
5. **“工具多”不等于“推理有效”。** 28+ 工具和全套 Kali 工具链降低环境门槛，但模型可能不会选工具、不会读大量输出或无法从错误反馈更新假设；需要工具选择和信息价值的实验评测。
6. **安全策略覆盖面不一致。** Web UI 登录、路径过滤和 Docker 都不自动代表执行隔离可靠。需要分别评估题目文件不可信、agent 执行权限、目标 host allowlist、宿主机凭据、Docker socket 和 egress 控制。
7. **产品级可复现指标不充分。** 解题率之外，费用、延迟、重复动作率、人工介入、可复现率、类别退化和误报仍少有统一公开报告。

## 4. 基础项目的选择建议

| ctfbot 目标 | 优先研究的基础 | 选择理由 | 主要风险/退出条件 |
|---|---|---|---|
| 做可替换模型的通用 CTF agent/研究框架 | EnIGMA v0.7.0 做可运行基线；必要时用 SWE-ReX 拆执行运行时 | 最贴近 CTF ACI、IAT 和 benchmark 的问题，能较快建立性能基线。SWE-ReX 提供独立的 session 与执行 backend。 | 如果固定旧版妨碍新模型接入、工具 schema 或验证流程，就停止深度 fork，改做薄 runner 并复用接口思想。先看 license、依赖和镜像。 |
| 做团队/赛事用的 CTF Web 工作台 | NUSGreyhats/ctf-agent-workstation 作产品参考，优先评估其平台适配层 | 已覆盖多 agent、人工 steering、题目/运行持久化、竞赛平台、工具环境和 Web/Discord 交互。 | 整体更重并绑定 Claude/Codex；GPL-3.0 许可证需评估；云 VM、VPN、平台凭据和工具权限扩大运维与安全面。只抽取设计或代码前明确产品形态和许可证要求。 |
| 做单人本地 TUI/课程练习工具 | AI-CTFer 作为 manifest、单题流程和运行产物参考；TUI 自建，solver adapter 可自建或替换 | `challenge.yml`、Docker、plan/notes/writeup 的单题流程简单，provider 配置清晰；ctfbot 可补上交互式运行监看和证据导航。 | 对多模型支持、session、领域覆盖、安全隔离和公开 benchmark 泛化先自行验证；不以当前 README 宣称代替试跑。 |
| 做既有模型运行时的 CTF skills 包 | Claude Code workflow 的 skills-first 方式；或 NUS 的 skill catalog 设计 | 适合尽快积累工具说明和操作流程，不需要重建模型调用层。 | 依赖具体宿主，难实现模型/供应商公平对照；不适合以通用 agent 框架为主要目标的 ctfbot。 |
| 研究多 agent 或经验库 | 参考 D-CIPHER、ctfagent、NUS 的并行协作，但先做单 agent baseline | 这些系统分别覆盖 planner/executor、领域专家、赛马和突破共享。 | 只有单 agent 固定预算对照显示净收益后才实现；检索答案型 writeup 与盲测基准必须隔离。 |

**综合建议：** ctfbot 当前应先做一个 adapter-friendly 的解题 runner，而不是复制某个完整产品。对 EnIGMA 做兼容性 spike；对 NUS 工作台做功能差距检查；借鉴 AI-CTFer 的题目 manifest；借鉴 CTF Cyber Agent 的证据 schema；使用 SWE-ReX 或类似成熟组件处理会话/执行，再将解题工具做小而清楚的 CTF ACI。只有当目标明确为 Web 工作台、赛事集成和团队赛马时，才评估基于 NUS 项目大规模改造。

## 5. ctfbot 可占据的差异化位置

建议把产品定位收敛为：**可替换 LLM 的 CTF 解题运行时与评测框架**，先服务单题解题与研究复现，再扩展工作台功能。

可形成的差异点：

1. 统一 `challenge manifest`、工具调用结果和解题轨迹格式，可适配公开 benchmark、本地附件和竞赛平台。
2. 工具返回短而结构化，原始结果及衍生产物均有路径、hash 和运行元数据；支持可恢复的 GDB/nc/REPL session。
3. 通过事实、假设和实验记录推动策略更新；确定性校验与轨迹复核共同确认“已解”。
4. 单 agent 起步，领域知识和工具包按需装配；并用消融数据决定是否投入专职 agent 或并行赛马。
5. 把公开题 benchmark 和隐藏/改编题分开；报告每类成功率、成本、时长、人工介入、可复现率及题目污染状态。
6. 将授权目标和容器策略设为运行时配置的一部分，而非仅在 prompt 中提醒。

这与现有项目的区别不应只是“工具更多”“agent 更多”或“支持更多题型”。更有说服力的优势是：在固定条件下，ctfbot 能用更少调用找到高信息量实验，保留可审查证据，并让同一条解题轨迹可以复现和比较。

## 6. 建议的竞品验证工作

正式决定 fork 与自研前，可做一次小型、同条件 PoC：

1. 固定 10–20 道开发题，覆盖至少 4 类；不把公开答案库检索内容注入 agent。
2. 对照 EnIGMA 基线、轻量单 agent runner、（若环境允许）NUS 的单 agent 工作流；保持模型、题目、时间/token 预算和容器镜像一致。
3. 记录每题精确 flag 校验、耗时、token/费用、工具调用数、重复动作、人工介入和复现情况，并按类别拆分。
4. 做运行时适配成本记录：首次跑通耗时、需要改动的模块、依赖/镜像问题、模型适配难度、许可证和安全边界。
5. 只有当多 agent 在相同预算下提升成功率或降低成本，才进入 planner/executor 或 race；只有当工具包装优于 shell baseline，才继续扩充定制工具目录。

## 7. 主要参考链接

- [EnIGMA / SWE-agent](https://github.com/SWE-agent/SWE-agent)；[EnIGMA 论文](https://arxiv.org/abs/2409.16165)
- [NYU D-CIPHER agents](https://github.com/NYU-LLM-CTF/nyuctf_agents)；[D-CIPHER 论文](https://arxiv.org/abs/2502.10931)
- [NUSGreyhats CTF Agent Workstation](https://github.com/NUSGreyhats/ctf-agent-workstation)；[设计说明](https://github.com/NUSGreyhats/ctf-agent-workstation/blob/main/DESIGN.md)
- [AI-CTFer](https://github.com/Archerui/ai_ctfer)
- [humaidhahm/ctfagent](https://github.com/humaidhahm/ctfagent)
- [CTF Cyber Agent](https://github.com/allen-haitao/ctf-agent)
- [Claude Code CTF-Agent workflow](https://github.com/nonoleekr/CTF-Agent)
- [SWE-ReX](https://github.com/SWE-agent/SWE-ReX)
- [NYU CTF Bench](https://github.com/NYU-LLM-CTF/NYU_CTF_Bench)；[Cybench](https://cybench.github.io/)；[InterCode](https://github.com/princeton-nlp/intercode)
- [CTFTiny](https://github.com/NYU-LLM-CTF/CTFTiny)；[CTFJudge](https://github.com/NYU-LLM-CTF/CTFJudge)
- [Cyber-Zero / EnIGMA+](https://github.com/amazon-science/cyber-zero)；[CTFAgent 论文](https://arxiv.org/abs/2506.17644)
