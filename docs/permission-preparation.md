# 临时权限准备与验收

2026-10-10。本轮仅处理依赖准备、权限阻塞的实机验收及其发现的问题；完成后暂停重构开发。

## 已落地

| 原阻塞 | 处理结果 |
| --- | --- |
| npm 网络不可用，无法可靠准备 SDK | 官方 npm 源安装 Pi SDK 1.1.0，精确开发依赖与 lockfile 固定传递依赖；项目优先加载本地安装 |
| 依赖全局 SDK，撤回权限后难以重建 | 保存 node_modules 与 data/npm-cache；实际执行 offline npm ci 成功，安装 121 个包，npm 报告 0 个已知漏洞 |
| Docker socket 不可访问，只有模拟验证 | 在真实 Docker 29.8.2 上完成隔离、脚本、工作流、图片、管道、超时、输出上限与恢复测试 |
| 网络工具无法实机验收 | 本机真实 TCP server 验证授权端点限制与 16 KiB 回复上限；独立断网容器验证 local-service loopback |
| Pi 与真实容器尚未串联 | 项目内真实 Pi SDK + 自建零费用 provider + 真实 Docker 完成附件读取、候选提交、writeup、证据审计与清理 |

保留 1.1.0 是为了匹配 reference/pi 的开发手册及本项目已经验证的接口；本轮无需升级 SDK。已安装发布包，无需构建或修改 reference/pi，也没有执行 npm 生命周期安装脚本。

镜像使用已有本地 `ctfbot-tools:candidate`，实际不可变 ID：

```
sha256:832c36c7a7494925a85f8dc975998e2b43db6410a5d7c2aafeab7d2227d437e9
```

镜像已在本机 Docker 镜像存储中；本轮未重新下载或重建。实际执行使用 image ID 与 `--pull=never`。

实机验收发现并修复：交互会话运行时 `exit_code: null` 被工具层误报为失败。现按会话状态判断，并保留失败、超时、输出超限的错误标记；新增普通回归测试与真实交互验收。

## 验证与复现

```sh
npm ci --offline --ignore-scripts --registry=https://registry.npmjs.org --cache=./data/npm-cache
npm test
npm run test:integration
npm run check
npm run doctor
```

普通测试 44 项、实机集成测试 5 项。集成测试需要非 root 控制器、可访问 Docker daemon 和上述本地工具镜像；可通过 CTFBOT_DOCKER_TEST_IMAGE 选择其他兼容本地镜像。测试使用临时自建附件与服务，结束后删除临时容器、目录并关闭监听，不需要真实模型密钥。

本轮最终检查输出保存在忽略提交的 `reports/permission-preparation-*` 文件。依赖缓存、已安装依赖与本地镜像属于当前机器的准备成果；换机器需重新安装或自行迁移缓存及镜像，lockfile 与测试代码可复现安装和验收。

## 撤回权限后的边界

- 已安装 SDK、离线缓存、锁文件、代码修复和测试将保留；普通代码开发、静态检查与大部分普通测试可继续。
- 容器创建、执行和清理每次都需要 Docker 权限。预装镜像无法消除运行期间访问 daemon 的需求；权限撤回后实机集成测试与真实解题可能再次受限。
- 真实远端访问和模型调用每次都需要网络权限；真实模型还需要用户配置的 provider 凭据及额度。本轮未读取或改写凭据，未调用收费模型。
- 真实 provider、模型图片传输、模型压缩、运行中 Stop、服务授权到期和真实远端断流仍未完成实机验收；它们不能由当前合成成功推断。
- 最终检查时根目录没有 `.git`，本轮未初始化仓库；此问题不影响本轮依赖与实机验收。

没有修改 Docker socket 权限、用户组、sudo 或系统安全策略，没有创建常驻代理或保留权限入口。准备阶段结束时已暂停；用户随后要求继续开发，后续结果见 [开发验收记录](development-validation.md)。
