# 单题真实解题 smoke：cry-babycrypto

日期：2026-09-30
结果：通过；这是一道题、一次运行的工程 smoke，不是批量 baseline 或通用解题能力结论。

## 授权和数据范围

- 用户批准一次向 ChatGPT/Codex 发送真实解题数据，范围为 cry-babycrypto 的 manifest-listed ciphertext.txt 和通用任务说明。
- 限额为最多 3 个模型回合、6 次工具调用、240 秒总 wall time。一次授权已消耗；其他题目、复跑和批量传输都要重新确认。
- 正式 pilot-manifest.json 未改动，所有题目的 model_data_authorized 仍为 false。本次使用 /tmp 下的一次性 manifest 和独立 snapshot；oracle 保留在 workspace 以外的 controller-only 私有目录。
- 运行没有读取 README、solver、writeup 或 oracle，没有执行 command_run，没有连接外部服务。

## 运行和结果

- Provider：本机 Codex CLI 0.158.0 / ChatGPT 登录，模型 gpt-6-luna，reasoning effort max，Codex App Server experimental dynamicTools。
- Runtime：固定本机镜像 ID sha256:61066f13fb37f4849473646835763de8de96d750769caf0797e7aaa6220d7b60；容器断网、根文件系统只读、challenge 只读挂载、/work 为限额 tmpfs。
- 结果：controller-side exact-string verifier 返回 verified；1 个模型回合，6 次工具调用（列目录 2、读文本 3、提交候选 1）。不在文档中记录答案。
- Provider 未返回 usage 对象，因此 token 数和费用未知。
- Run ID：76864016-eb19-4882-acde-e00fed562453。本机私有证据目录：/tmp/ctfbot-stage-a-chatgpt-runs/0cc84d05-0ce8-460a-baf0-b3f032426d06；目录权限 0700，事件文件权限 0600。

## 数据边界发现

工具调用轨迹显示，模型除读取通用任务说明和 ciphertext.txt 外，还读取了 workspace 根目录的 provenance.json。该文件仅含此题的 ID/category、来源与输入哈希、导入模式、准入状态和本次授权说明；不含 oracle、flag、README、solver、writeup 或其他题目数据。它超出了原定“仅任务说明和题目附件”的最小传输范围。

已修改 baseline wiring：Docker runtime 的 challenge mount 与模型文件工具都只使用 workspace/input 子目录；TASK 仍作为初始 prompt 发送，workspace 根 provenance.json 和其他 controller 文件不在容器或模型文件工具可读路径中。已运行一条离线 synthetic 单测覆盖真实 run_baseline wiring、工具路径策略与 Docker mount 参数，测试通过；未启动 Docker 容器或调用 provider。

## 结论和限制

本次证明当前 agent loop、Codex App Server tool callback、断网 Docker runtime 和 exact-string controller verifier 能在这一道静态加密题上完成端到端运行。它没有验证 provider usage/cost、错误/超时/取消语义、工具策略修复、Docker 宿主隔离安全、复跑稳定性或批量 solve rate。任何后续真实题目调用或复跑需取得新的明确授权。
