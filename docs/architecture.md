# 重构架构与决策

开发前已阅读 reference/pi/packages/coding-agent/docs/sdk.md、extensions.md、sessions.md，以及 SDK 的模型、工具、资源、会话和全控制示例，并核对 sdk.ts、extensions/types.ts、agent-session.ts。参考 API 是 @earendil-works/pi-coding-agent 1.1.0，不是历史 @mariozechner 包。

使用 Node 22 ESM JavaScript；主业务无第三方依赖。边界采用显式 JSON Schema 和运行时校验；Pi SDK 单独延迟加载，UI 使用固定版本的 pi-tui 和 pi-ai 模型能力辅助函数。Python 旧框架、Codex App Server 适配、旧控制器循环与自建上下文压缩均不再使用。解题脚本可以使用沙箱内的 Python 等语言，控制器本身只使用 JavaScript。

依赖方向：CLI/UI → application → challenge/evidence/runtime/tools；pi 适配只连接 SDK 与业务工具。业务模块不导入 Pi，允许无模型测试。工具声明、schema、handler 位于同一模块，扩展不读取 Python 工具注册表。模型、认证、重试、转向、会话历史、压缩由 Pi 管理；一个 run 一个 Pi session，无第二套推理循环。

## 关键迁移冲突

- Pi 默认 read/bash/write/edit 在宿主执行：使用受控 ResourceLoader、tools=[]，仅注册沙箱工具；禁用自动资源、扩展、MCP 发现。不启动任意项目插件。
- 不调用 Pi CLI 的完整交互模式：它的宿主 bash、会话切换、分享命令与单题隔离冲突。终端工作台基于 Pi TUI 组件重新布局，提供服务认证、模型与思考强度选择、题目导入及内嵌报告，共用 application 服务；ModelRuntime 在配置与运行之间共享，认证只由 Pi 处理。
- Pi 压缩会使用模型，旧控制器的无模型快照替换不移植。Pi 自动压缩保留原始会话；业务证据和 state_read 独立保留。该行为可能产生额外模型额度。
- 旧 README 同时存在 candidate_unverified 和 completed 契约：按最新 reliability.py，提交不自动停止，完成必须保存 writeup。completed 不声称平台验证，partial 必须列出真实未完成子题。
- 旧私有 workspace 仅兼容 admitted + static_files_only + 哈希 + 非空授权依据；本地/远端服务 workspace 不可作为离线任务误导入。
- 旧服务验收 profile、远端 grant、记忆和 dataset 有独立版本，不能静默把旧授权解释成新授权。支持情况和替代路径见 migration.md。
- 不设置整题轮次、token 或总时长额度；单次命令/后台进程/输出依然有保护。停止、异常、自然结束都必须关闭容器。容器清理失败保留 recovery.json，doctor 和 recover 可恢复。

## 数据约定

私有 imports 下的 input、TASK.md、manifest.json 为题目快照，运行前和快照拷贝后重新校验。工作容器只挂载该 run 的 input，只读根文件系统、无网络、无 capability、非 root，/work 为 tmpfs。凭据、控制器工作目录和 Docker socket 不进入容器。

证据为版本化 events.jsonl + SHA256 命名 artifacts；工具完整返回先保存再截断到 16 KiB 给模型，读取提供字节覆盖范围。公开摘要/claim 是模型报告，源绑定仅验证字节一致性，不是正确性证明。Pi transcript 独立放在 run/pi。report 默认只列候选 ID；writeup 和候选原文为私有数据。

## Workflow 机制

领域分类是浏览入口；方法卡按观察匹配并提供前提、反证、最小实验和分支。检索共用内置方法及受审 memory 目录，按需读取，不建立固定路线或第二套 agent 循环。确定性操作使用版本化参数契约，所有附件解析继续留在沙箱。

RunState 增加 assets：原始输入与派生内容采用统一引用，派生文件以证据快照保存；后续操作从快照重新物化并验证哈希，不依赖原工作路径。现有实验与摘要增加方法、假设、能力和阻塞关联，重复执行只提示不禁止。详细契约见 [workflows.md](workflows.md)。

上下文分为静态系统/工具、压缩历史与历史状态快照、当前任务/状态/参考索引、近期原始交互。每 10 个完成的解题轮次整理当前状态，达到 Pi 窗口阈值才压缩较早交互；两者共用结构化维护器。见 [context-management.md](context-management.md)。
