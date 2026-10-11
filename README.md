# FlagCapturer 0.2

基于 Pi SDK 的 JavaScript CTF 解题工作台。旧 Python agent 框架已被替换；模型调用、工具循环、会话、重试和上下文压缩由 Pi 提供。题目导入、Docker 隔离、领域工具、证据、Flag 与 writeup 为独立业务模块。原项目位于 `reference/`，新实现不调用旧 Python 控制器。

当前提供单题 CLI 和重新设计的终端工作台、六类入口与跨题型方法检索、可追踪的中间产物链、长脚本与交互管道、图片输入、公开摘要与实验、原始证据、报告、诊断、命令回放、无答案评测、受限 TCP/本地服务、受审记忆。Workflow 的方法、产物和操作契约见 [workflows.md](docs/workflows.md)。完整迁移差异见 [migration.md](docs/migration.md)，设计依据见 [architecture.md](docs/architecture.md)。工作台支持自适应双栏、可搜索菜单、Pi 原生指令/文件补全、API/OAuth 认证、模型与思考强度选择、内嵌报告浏览，使用说明见 [workbench.md](docs/workbench.md)。

## 开发与验证

Node.js >=22.19，业务层使用 Node 标准库，界面复用 Pi TUI，Pi 相关包固定为 1.1.0：

```sh
npm test
npm run check
npm run test:integration # 需要 Docker 和本地 ctfbot-tools:candidate 镜像
node src/cli.js doctor
node src/cli.js --help
```

Docker 工具箱构建与离线能力清单见 [docker/tooling/README.md](docker/tooling/README.md)。

测试只使用自建合成数据，不调用收费模型、不执行真实题目。普通测试使用可替换边界和本地进程，集成测试使用真实 Pi SDK、Docker 和本机 TCP 服务；可通过 `CTFBOT_DOCKER_TEST_IMAGE` 指定本地镜像。`check` 解析所有新 JS 模块并检查相对导入，**不等同 TypeScript 类型检查**。源代码为 ESM JavaScript，工具参数通过 JSON Schema 和业务约束校验。

## 准备 Pi 和 Docker

适配目标是本仓库参考版本 `@earendil-works/pi-coding-agent` **1.1.0**，已由 package-lock.json 固定。加载顺序：`FLAGCAPTURER_PI_SDK` 指定的构建入口 → 项目 node_modules → `reference/pi/packages/coding-agent/dist/index.js` → 当前 Node 全局目录内的同名 SDK → npm 包解析。

当前工作区已安装 SDK 并缓存依赖，撤回网络权限后可在同一环境离线恢复：

```sh
npm ci --offline --ignore-scripts --registry=https://registry.npmjs.org --cache=./data/npm-cache
```

首次在其他环境安装时使用 `npm ci --ignore-scripts --registry=https://registry.npmjs.org --cache=./data/npm-cache`。缓存与 node_modules 不提交仓库。

可在网络可用且依赖安装完成的环境构建参考项目（先按其 AGENTS.md 审阅安装脚本和构建要求）：

```sh
npm --prefix reference/pi ci --ignore-scripts
npm --prefix reference/pi run build:offline
```

也可在该版本已发布的 registry 安装精确版本：

```sh
npm install --no-save --ignore-scripts @earendil-works/pi-coding-agent@1.1.0
```

本仓库把 Pi 声明为可选 peer dependency，并以精确版本 dev dependency 准备开发环境，允许未安装模型运行时的环境先运行普通测试、导入、预览、报告。没有 SDK 时 solve 明确报错，不回退到 Python agent。Pi 认证可在本工作台 `/login`、Pi 自身 CLI 或 provider 环境变量中配置，ctfbot 不复制旧 Codex token。使用参考版本的模型 ID；不假定旧 App Server 的模型名称可直接复用。

解题要求非 root 控制器、可访问的 Docker daemon、本地已构建的工具镜像。可沿用 reference/ctfbot 的 general-v3 镜像及其构建说明。镜像必须包含 **Python3（进程监督）、Node（领域工作流）**；图片查看另外需要 Pillow。启动前解析本地 image ID，始终 `--pull=never`，不自动下载镜像或安装依赖。

## 导入和解题

```sh
node src/cli.js import ./attachment.bin \
  --authorize-model-data --basis '本人允许本题附件与工具输出发送给所选模型' \
  --task '分析附件并找出题目要求的 Flag，以中文编写 writeup'

node src/cli.js preview data/imports/<snapshot>
node src/cli.js solve data/imports/<snapshot> \
  --provider <pi-provider> --model <pi-model-id> --confirm-model-usage \
  --interactive --additional-prompt '已知线索……'
```

单文件或仅含一个文件的目录均可导入，单附件 1 byte..32 MiB。未授权时仍可导入和预览。运行前再次校验任务及所有附件的哈希，并复制到独立运行快照。旧 admitted/static_files_only workspace 的 TASK.md、provenance.json、input 可直接预览和运行，授权依据必须仍有效。传 `--image` 可覆盖默认 `ctfbot-tools:candidate`；镜像仅做本地 inspect。

直接 `npm start` 打开工作台：`/login` → `/model` → `/thinking` → `/import <路径>` → `/run`。支持 API key、服务提供的 OAuth 和 `/endpoint` 自定义兼容 API，配置复用 Pi 认证。运行时 `/stop` 或 Ctrl+C 停止，`/state` 查看状态，`/flags` 查看完整候选，其他文字作为 steering 输入。公开说明和工具事件实时显示；隐藏推理不显示。终端显示过滤控制字符，候选原始字节仍保留在证据中。没有整题轮次、token、总时长限额；单次命令 120 秒，后台进程最多 30 分钟、16 MiB 输出，最多 8 个并发会话。后台会话为管道，不是 PTY。主动 `session_close` 返回 closed，保留实际退出码；不会把主动终止误报为工具失败。

Flag 提交不检查格式或比赛平台接受情况。每个 Flag 提交后继续实际剩余子题；完成必须包含所有候选 ID 和 agent 编写的 writeup。completed 退出 0；partial/unsolved/错误退出 1；手动停止退出 130。Pi 压缩可能额外使用模型额度，与旧实现的无模型快照方案不同。

## 证据、恢复和评测

```sh
node src/cli.js events runs/<run-id>
node src/cli.js audit runs/<run-id>
node src/cli.js diagnose-run runs/<run-id>
node src/cli.js report runs/<run-id>
node src/cli.js recover runs/<run-id>
node src/cli.js replay runs/<run-id> --confirm-execution
node src/cli.js evaluate ./dataset.json \
  --provider <provider> --model <model> --confirm-model-usage \
  --output reports/evaluation.json
```

`runs/<id>/` 包含 events.jsonl、SHA256 artifacts、challenge 快照、Pi session、metrics.json、result.json、writeup.md。report 默认仅显示候选 ID。清理不确定时保留 pending recovery.json，recover 按 run ownership label 确认归属后删除容器。回放在新容器重放脚本、命令、工作流、交互输入，不自动重放网络访问或模型决策，不声称输出等价。

评测 dataset v1：`{"schema_version":1,"challenges":[{"id":"example","workspace":"data/imports/<snapshot>"}]}`。统计完成情况和每题结果、证据与 metrics，不衡量 Flag 正确率。每题仍需单独 workspace 授权，不继承旧 v3 dataset 隐含许可。详细授权格式见 [运行配置](docs/configuration.md)。

## 当前验证边界

继续开发后，80 项普通测试、13 项集成测试及模块检查通过。测试覆盖真实 Pi + Docker 合成解题、自动上下文压缩后状态恢复、多模态 provider 边界的图片传递、运行中 Stop、立即关闭交互会话、工作流、超时、输出限制、TCP 断流与取消、本地服务授权到期、回放和恢复清理，没有发送真实模型请求。详见 [开发验收记录](docs/development-validation.md)；依赖准备与权限边界见 [权限准备记录](docs/permission-preparation.md)。真实收费 provider、其图片协议序列化、真实比赛远端与解题率尚未验收。
