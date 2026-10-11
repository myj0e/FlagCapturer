# Python ctfbot → Pi JavaScript 迁移

本次为新架构实现，不是 Python API 的逐行翻译。reference 中两份项目仅作参考。开发前对照最新 tools/reliability.py、agent/loop.py、runtime/docker.py、packs/scripts.py、application/service.py、reporting/replay.py 和 benchmark/evaluation.py；旧 README 的历史状态描述不作为唯一依据。

| 原功能 | 新实现与差异 |
|---|---|
| 自建 agent loop、model_adapters、Codex App Server | 移除，src/pi/session.js 使用 createAgentSession、ModelRuntime、SessionManager |
| 自建上下文占用/快照压缩 | 使用 Pi compaction；业务状态独立持久化，通过 state_read 找回。Pi 总用量与 context 分开保存在 metrics |
| 单文件导入、预览、模型传输许可 | workspace.js；兼容 admitted/static_files_only 旧 workspace；哈希复核及 run 私有快照 |
| Docker 沙箱 | 断网、只读、非 root、cap-drop、tmpfs、固定 image、进程组监督；服务容器独立 |
| command_run | argv 执行；原始 stdout/stderr 证据、单次 deadline、截断标记 |
| session_start/send/read/close、script_save/run/start | 使用统一 supervisor；管道 IO、持久化 transcript、最多 8 个 live sessions；主动关闭返回 closed 并保留实际退出码；后台输出 16 MiB 单次保护，达到时终止该进程（旧版保留尾部继续跑的行为改变） |
| 六类 pack、workflow_discover/read/environment/run | 保留 playbook 和许可信息，执行程序重写为 JavaScript；RSA 大整数必须用十进制字符串；ZIP64 与多卷 ZIP 需通用工具；基础 workflow 不代表通用解题覆盖率 |
| image_view | 仅图片模型注册，Pillow 在沙箱解码与 EXIF/crop；返回真正 image block；最大源图 16M pixels，单次视图 4M pixels/1 MiB，超限要求裁剪 |
| artifact_export / evidence reads | SHA256 证据、输入 lineage、字节覆盖、分页读取、literal find/tail |
| candidate_submit/check、多 Flag、run_complete | 原文提交，局部关系检查不证明正确性；必须汇总所有候选并保存 writeup，结束后拒绝工具 |
| summary_update、observation、claim、experiment | 公开记录；源绑定使用 source_ref + parameter_key，验证字节/参数一致性；旧手填 locator/binding 不作为兼容接口 |
| 报告、诊断、审计、回放 | 私有报告、证据完整性、prompt 引用、故障清理；回放工具输入与脚本，不自动重放网络/模型/候选决策，不声称等价 |
| 无答案评测 | 新 answer-free v1 dataset，逐题运行，不计算正确率；旧 v3 dataset 需显式转换 |
| 远端 TCP、http_request | 新版本 scoped grant + controller broker，无 DNS、重定向或代理解释；不会把远端网络连接交给 solver 容器 |
| 固定本地服务、服务恢复 | 新 local-service grant 启动独立离线容器，broker 通过容器内部 loopback 交换；需要 Python3；没有端口发布/宿主 bind mount。授权到期自动停止服务容器；服务本身同样 30 分钟保护；旧 C3/C4 acceptance/profile 不自动认可 |
| 受审 memory | 新显式 JSON snapshot，校验 scope/hash/license/review，拒绝 holdout；无自动搜索网络或写入；旧记忆管理 CLI 不兼容 |
| Textual TUI | 基于 Pi TUI 重新设计工作台，自适应主区/侧栏、可搜索菜单、多行输入、steering/stop、内嵌状态/报告浏览；不复刻 Textual 的 F2/F3 或桌面剪贴板集成，共用 solve |
| llm setup/status/test/tool-smoke | 工作台支持 Pi API key/OAuth、模型和思考档位选择、自定义兼容 API；认证交给 Pi ModelRuntime；doctor 仅检查 SDK/本地镜像/daemon，不调用模型 |

## 维护和后续验收

新增工具必须声明 schema、handler、证据策略，并通过 registry 调用；共享状态的工具 sequential。领域解析放在 sandbox asset，不在 controller 导入附件解析器。新增 provider 使用 Pi，不添加第二套模型循环。

已实现的接口不表示完成真实解题率评测。已通过真实 Pi SDK 1.1.0 + Docker 合成解题、自动压缩后业务状态恢复、合成多模态 provider 的图片传递、运行中 Stop、立即关闭会话、Docker timeout 后工作目录保留、owned-container recovery、真实本机 TCP 断流/取消、本地服务隔离与授权到期、命令回放；当前验收见 [development-validation.md](development-validation.md)，历史依赖准备见 [permission-preparation.md](permission-preparation.md)。真实收费 provider、图片协议序列化、真实比赛远端和解题率仍未验收。当前没有自动沿用 Python TUI、旧 profile、旧 dataset 或旧 App Server 凭据的路径。
