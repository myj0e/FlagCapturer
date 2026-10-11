# 继续开发与验收

2026-10-10，用户要求继续开发后，在已安装的 Pi SDK 1.1.0 基础上补齐生命周期与真实 SDK 接口验证。开发前重新核对 sdk.md、extensions.md、sessions.md、compaction.md 和 AgentSession/SessionManager 源码；reference 项目保持原样。

## 修复

- 进程监督先检查持久化取消意图，在发布 PID 后再次检查，消除会话刚启动就关闭时尚无 PID 的竞态。关闭会话的并发调用共用一次终止操作。
- 主动关闭会话返回 closed，保留实际退出码，工具层不会把预期的进程终止当作失败；自然失败、超时、输出超限仍作为错误。后台证据持久化失败会通知应用并终止容器内进程。
- NetworkBroker 关闭立即拒绝新请求并取消进行中的 TCP/本地交换，等待已经开始的容器启动结束再清理。grant 到期自动关闭 broker 与服务容器，求解容器仍可离线工作；清理错误传回应用。
- 回放记录 run_started/run_finished 和每个操作的错误状态，CLI 用失败退出码反映实际命令错误，支持 --runs-root。操作失败和清理失败均保留；清理不确认时 recovery.json 继续为 pending，不会覆盖成成功。
- Pi 1.1.0 实际压缩事件是 compaction_start/compaction_end，已修复先前订阅旧事件名造成的审计遗漏，并记录摘要重试事件。

这些改动集中在 runtime、Pi 适配和 application 生命周期，工具与业务状态仍独立于 Pi，没有新增 agent 循环。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| npm test | 80 项通过，0 失败、0 跳过 |
| npm run test:integration | 13 项通过，0 失败、0 跳过 |
| npm run check | 51 个 JS/ESM 模块解析及相对导入检查通过 |

集成测试使用真实项目内 Pi SDK、真实 Docker、本机 TCP 与自建附件；provider 使用进程内合成实现，未使用真实凭据或模型额度。

新增验证包括：真实 Pi 自动压缩长历史后，state_read 仍可恢复已有候选，原始证据哈希保持有效，任务正常完成；真实图片从 Pillow 渲染，经 Pi 工具结果进入合成多模态 provider 上下文；Stop 中断真实工具操作并清理容器；立即关闭会话阻止后续写入；真实 TCP RST 拒绝、关闭取消等待中的 peer；local-service 授权到期自动删除服务容器；合成解题完成后在新容器回放实际命令。

普通测试另外覆盖取消发生在 PID 发布前和发布中的两种顺序、并发关闭幂等性、启动尚未结束时关闭 broker、回放操作失败与清理失败同时发生。

最终输出保存在忽略提交的 reports/workbench-unit.tap、workbench-integration.tap、workbench-check.log。标准复现命令：

```sh
npm test
npm run test:integration
npm run check
```

当前受限沙箱内可完成普通测试和模块检查。Docker 和本机 TCP 集成测试通过平台对具体测试命令的权限审核后执行，未修改 socket 权限或系统策略。离线缓存与 SDK 不受这次权限切换影响。

## 仍未声称完成的验证

合成 provider 验证 Pi 调度、压缩与数据传递契约，不能替代真实 provider 的 HTTP 图片序列化、认证、重试及模型表现验证。真实比赛远端与解题率尚未评测。旧 Textual 按键与桌面剪贴板集成、旧授权/profile/dataset 的静默复用依然不在当前兼容范围，详见迁移表。

## 本次工作台重设计

开发前追加核对 Pi sdk.md、API key/OAuth 与自定义模型示例、ModelRuntime 认证接口、模型思考档位及 Pi TUI 组件契约。Pi coding-agent、pi-ai、pi-tui 保持 1.1.0，依赖已写入 lock 并缓存。

替换旧配置向导，新增自适应主区/侧栏、多行输入、可搜索菜单和内嵌文档。认证使用 Pi 标准 API key/OAuth 交互，支持等待浏览器、设备码与人工回填、取消清理和秘密输入遮蔽；模型与思考档位按 provider 能力展示，包含显式支持的 xhigh/max。配置与运行共享同一 ModelRuntime，运行中可修改思考档位与补充线索。自定义兼容 API 的非敏感配置原子保存，SDK 验证或文件保存失败不会留下新 provider 半配置。

新增普通测试覆盖真实 Pi 临时凭据存储（全部为假密钥）、合成 OAuth、人机认证取消与等待替换、ID 精确选择、控制序列过滤、中文响应式布局、极小窗口、多行光标、真实 Pi TUI 的菜单输入/缩放/退出、停止等待清理以及隐藏推理不进入公开记录。额外使用真实 PTY 手动验证启动、搜索菜单和退出；未登录真实账户。

新增集成测试通过工作台驱动真实 Pi 与 Docker 完成自建合成题，验证共享 runtime、工具事件、候选侧栏、产物审计及容器清理。所有 12 项集成测试均通过；后续配置回滚和多行光标补丁由完整 63 项普通测试再次验证。运行说明见 [workbench.md](workbench.md)。

## 登录流程修复

`/login <provider>` 现在使用 Pi provider ID/名称直接定位服务，支持显式 `oauth` / `api_key`，同时支持两者时默认选中 Pi 声明的 OAuth 登录选项。授权 URL、设备码、本地回调、token 交换与保存仍由原生 Pi provider 和 ModelRuntime 处理；新增的浏览器适配只执行与 Pi 相同的无 shell 系统启动命令（SDK 的 openBrowser 是私有工具，没有公共导出）。

收到 auth_url/device_code 后自动打开浏览器，重复通知不重复启动。没有桌面或启动失败时保留链接和手动流程。登录链接不会被后续轮询通知挤出，也不会只保留末尾六行；等待窗口及人工回调输入窗口均支持分页查看完整链接。等待窗口 Enter 重试打开浏览器，Esc 取消整个认证；正常切换到人工输入不会误取消认证。

本次完整普通测试 70 项通过、46 个模块检查通过。新增验证覆盖跨平台浏览器 argv、启动失败、OpenAI provider 参数与认证选项、链接去重/重开/分页、手动回调输入及取消。额外通过真实 Pi ModelRuntime 和 OpenAI 原生 provider 元数据，以合成授权边界验证回调胜出后取消人工输入、完成认证并保存临时 OAuth 凭据。未请求真实 OAuth 服务、未打开真实登录页面、未使用真实账户。本次未修改 Docker 执行逻辑；此前 12 项真实集成测试记录保留。最新日志为 reports/login-unit.tap 与 reports/login-check.log。

## ChatGPT 安装身份修复

定位到此前只传 agentName，遗漏 Pi 原生 OpenAI ChatGPT OAuth 必需的 LoginOptions.getDeviceId，导致生成浏览器链接前即抛出设备 ID 缺失错误。现复用 Pi SettingsManager.getOrCreateDeviceId，从全局安装设置获取/创建 UUID，忽略项目设置中的身份覆盖；登录前 flush 并检查保存错误，API key 登录不创建设备 ID。认证失败提示按已知原因分类，不回显可能含凭据的任意服务正文。

新增测试直接复现未传设备 ID 时未修改的原生 OAuth 错误，验证设备 ID 跨设置重新加载保持一致、错误设置与保存失败不启动认证、诊断脱敏。另新增完整原生 ChatGPT OAuth 集成测试：保留 Pi 的安装身份校验、PKCE、state、实际本机 1455 HTTP 回调、授权码解析、token 响应/授权范围校验、人工输入取消及凭据保存，只拦截外部 token HTTP 请求为合成响应，浏览器由本机 HTTP 回调模拟。测试未使用真实账户或模型额度。74 项普通测试、49 个模块检查通过；日志为 reports/login-device-unit.tap、login-device-integration.tap、login-device-check.log。

随后完整集成套件 13 项通过、0 失败、0 跳过，涵盖新增原生 ChatGPT OAuth 与原有 Pi/Docker/TCP 验证；完整日志为 reports/login-device-all-integration.tap。

## Pi 输入补全接入

主输入框接入 Pi CombinedAutocompleteProvider、fuzzyFilter 和 Editor 原生补全交互。应用只提供已有命令、动态 provider/model/思考档位及路径参数，不维护第二套候选菜单或文件遍历器。`/model provider/model-id`、`/thinking level` 现支持直接参数，补全后的命令能执行对应操作；路径参数处理 Pi 生成的双引号、空格、@ 引用与 ~ 展开。已安装 fd 时使用 Pi 的原生递归搜索，没有时为 @ 引用复用原生目录补全，不启动自动下载。

补全界面为原生 Editor 预留空间，窄屏和多行时优先保留输入光标与选中项。六项新增测试覆盖命令集合、动态参数、Tab 强制补全语义、取消、中文及空格路径、目录引号光标位置、@ 引用、不读取文件内容、控制字符过滤、实际 Pi Editor 的 Tab/方向键/Esc、窗口缩放和参数落地。普通测试 80 项通过，51 个模块检查通过。真实 PTY 手动确认 `/thi` 候选与 `/import ./sr` → Tab → `/import ./src/`；没有提交导入、登录或模型请求，没有改变已有模型选择。日志为 reports/autocomplete-unit.tap 与 autocomplete-check.log。

补全接入后的完整集成测试 13 项通过、0 失败、0 跳过，覆盖原生 OAuth、工作台 Pi/Docker 解题及 TCP 生命周期。日志为 reports/autocomplete-integration.tap。

## 2026-10-11 最终验收

工具按需供应、思考展示及全局细节修改后，统一执行一次完整验收：122 项单元测试、20 项集成测试、76 个模块检查全部通过。新增测试仅下载 5621 字节的锁定 Base58 wheel，未构建、拉取或更换镜像，未使用收费模型。记录见 [project-review-20261011.md](project-review-20261011.md)，日志保存在 `reports/final-acceptance-20261011/`。
