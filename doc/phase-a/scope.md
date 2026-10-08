# 阶段 A1：授权范围与威胁边界

状态：范围文档完成；CTFTiny 的静态/离线附件 importer 与 A 阶段 snapshot gate 已实现，Compose 和远端 allowlist 仍只是设计边界，未实现。

## 支持的挑战模式

| 模式 | 可接受输入 | 默认允许 | 必须拒绝 |
|---|---|---|---|
| 本地附件（`local`） | 用户提供的题目目录、附件、说明和 flag 格式；导入时记录路径与 SHA-256。 | 对导入副本做静态分析；在隔离 workdir 执行分析命令与临时脚本。默认网络关闭。 | 把附件目录以可写方式暴露给 agent；访问导入目录外路径、宿主敏感目录或未声明网络。 |
| 本地服务（`compose`） | 用户提供的 Compose 服务，或经审核、锁定 digest 的 challenge image；记录授权来源。 | 建立每题独立网络；只允许 agent 连接本题服务及其明确端口；测试结束后清理服务、网络和 volume。 | `privileged`、host network、Docker socket、宿主敏感 mount、未审查 build script、默认公网出站、跨 case 网络访问。 |
| 远端 allowlist（`remote`） | 用户明确授权的练习平台/比赛题；host、port、protocol、有效期、授权者和确认时间均须显式提供。 | 只访问声明的目标；每次解析、重定向和连接前复核范围；遵守目标服务规则与速率限制。 | 空 allowlist、任意公网目标、扫描旁路网段、DNS/重定向后超范围目标、未获授权的第三方系统、自动提交 flag。 |

分类标签（crypto/web/pwn 等）仅用于选择提示和工具包，不能决定网络权限。模型、题目描述和附件都不能自行扩大授权范围。

## Controller 与执行环境边界

- Controller 保存 provider 凭据、用户确认和 run 元数据；执行容器不接收 provider key、SSH agent、宿主 home 或 Docker socket。
- 附件以只读方式提供；临时脚本和转换文件写入独立 work 目录。容器以非 root 用户运行，限制 CPU、内存、PID、磁盘、时长、输出和并行 session。
- 本地附件默认无出网；远端题的网络规则必须由 runtime/防火墙/受控代理执行。单独包装 HTTP 工具不足以限制 shell 中的 curl/nc。
- 不把普通容器承诺为高保证安全边界。若题目包含恶意二进制、可攻击服务容器或要求利用内核/容器逃逸，首版拒绝运行，后续评估 disposable VM 或更强隔离。
- 所有不可信文本在 TUI 中按纯文本安全显示；审计保留原始字节/文件 hash，展示层不得解释 ANSI/OSC 控制序列。

## 用户授权与拒绝行为

授权应绑定到 challenge/run 快照，并显示题目来源、附件 hash、环境、目标范围及有效期。授权缺失或检查失败时，导入可以完成，但运行不得进入 `ready/solving`；记录 `policy_denied` / `environment_error` 和具体原因。TUI 的确认按钮只确认已展示的范围，不自动生成或扩展 allowlist。

当前执行代码只覆盖固定 commit/hash 的静态附件导入、oracle 分离、准入/题目数据传输授权检查、只读附件工具和离线 Docker profile；它不是通用 challenge manifest validator，也不支持 Compose 服务网络或远端 allowlist。策略文档中未由代码和对应测试覆盖的约束仍只是设计要求，不得当作现有安全控制。
