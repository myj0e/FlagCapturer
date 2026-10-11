# 沙箱工具按需供应：方案评审与实现

原方案的核心方向成立：由控制器准备工具，解题环境保持离线、非 root、受控挂载，工具准备与题目网络授权分离。以下为已实现的调整，不再是待开发建议。

## 评审结论与调整

每次申请都构建工具镜像，会重复安装基础环境、下载大型发行包，并增加构建网络治理和镜像维护成本。当前 general-v4 已有 OCR、Ghidra、Sage、Foundry 等重型工具，因此优先复用它，缺少的小型 Python 能力采用只读扩展目录交付。

**不更换、不重建、不自动拉取当前镜像。** 主沙箱和已有进程继续运行。扩展执行使用同一不可变 image ID，加挂经过校验的只读 Python 目录；不向主解释器注入依赖。

原方案“仅构建容器访问允许仓库”调整为更严格、较简单的实现：控制器只下载内置锁文件列出的确切 HTTPS wheel，所有安装容器与执行容器均为 `--network=none`。不调用宿主 pip、不运行宿主安装脚本，也不让依赖解析器自由联网。

暂不提供任意包名/版本、URL、源码构建、原生系统包、Node/Ruby 动态依赖或自动权限升级。新增能力需维护者补充固定配方、完整依赖闭包及组合验证。这个边界使行为可预测，不能将它描述成“自动安装所有工具”。

## 使用流程

1. `workflow_environment` 检查具体方法的前置条件；`tool_request({capabilities: []})` 返回供应目录。
2. `tool_request({capabilities: ["ocr"]})` 申请能力；支持目录中的别名，如 `cv2`、`tesseract`。请求立即返回准备 ID，重复请求复用同一任务。
3. `tool_status({preparation_id, wait_seconds: 2})` 查询进度，最多等 10 秒。状态依次为 queued/checking/downloading/installing/verifying/ready；命中当前镜像或缓存可跳过下载和安装。`cancel: true` 取消尚未完成的任务。
4. ready 返回 `environment_id`，可通过 `tool_exec` 执行完整脚本。主沙箱的 `command_run` 不自动改变解释器环境。
5. 输入通过已校验的 asset ID 指定，输出声明后导回证据与 asset 状态。没有自动共享 `/work`、原始附件目录或交互会话。

当前目录包含 OCR、逆向、密码、取证、信号、Web、区块链能力组，以及 Construct、dpkt、pefile、Base58 四个固定 wheel 配方。前三个小库当前镜像已有，通常无需下载。Base58 wheel 约 5.6 KB；版本、来源、大小和 SHA-256 在 `assets/tool-wheels.lock.json`。

```json
{"capabilities":["base58","ocr"]}
```

能力组合在同一环境里检查命令、实际 import、配方功能及 `pip check`，适合 Python 库与本地程序协作。无法补齐的原生能力返回明确原因，不能以某个库安装成功替代整个环境验证。

## 执行与文件衔接

`tool_exec` 每次启动临时离线容器，复用固定基础 image ID；容器内 `/challenge` 为空目录，仅复制指定的、哈希校验后的输入到 `/work/inputs/<name>`。根目录和扩展目录只读，工作区为独立 tmpfs。

先用 `script_save` 保存源代码，再传 `script_id`，源代码会复制到 `/work/tool-script.py`、`.mjs` 或 `.sh`。这里 Python 应使用普通 `python3`；`-I` 会主动忽略扩展所需的 PYTHONPATH。

```json
{
  "environment_id":"<tool_status 返回的 ID>",
  "argv":["python3","/work/tool-script.py"],
  "script_id":"scripts-1",
  "inputs":[{"asset_id":"assets-1","name":"sample.bin"}],
  "outputs":[{"path":"/work/result.bin","display_name":"提取的数据"}]
}
```

实际脚本 ID 以 `script_save` 返回值为准。输出必须关联输入资产；每个资产最多 16 MiB，最多 16 个输入/输出。成功执行时导出声明的文件；失败仍保留 stdout/stderr 和执行元数据。未声明的临时文件在容器销毁时消失。导回的资产可以直接进入原有工作流，按需重新物化到主沙箱。传入 `inputs` 的文件名不能重复。

## 来源、缓存与资源

- 只允许锁文件中确切的 `files.pythonhosted.org` HTTPS URL，禁止重定向；验证实际字节数与 SHA-256，单 wheel 上限 8 MiB。请求不携带附件、工作区、模型凭据或代理安装命令。
- 下载后在无网络、非 root、只读根的独立容器中，以固定参数 `pip --no-index --no-deps --no-compile --target` 安装 wheel。安装时只有私有 staging 目录可写，没有题目输入或 Docker socket。
- 每次运行最多 8 个不同请求，串行准备，准备总时限 5 分钟；安装命令 120 秒；容器 2 CPU、2 GiB、256 PID。完整扩展清单最多 4096 文件、128 MiB；持久缓存最多 16 个环境，超限时返回原因，不自动清除其他运行可能使用的缓存。
- 缓存键包含不可变基础 image ID、配方、版本/来源/哈希和供应格式版本。image ID 固定其架构；当前新增 wheel 均为 pure-Python 通用包。
- 验证成功后原子发布缓存，缓存默认位于项目 `data/tool-cache`。复用前和每次执行前重新校验完整文件清单，拒绝符号链接、特殊文件、篡改和超限内容。
- 失败和取消不会成为 ready，当前运行保留失败结果，重复请求不重新下载。修复外部条件后可开始新运行。正常取消会清理 staging；控制器异常退出遗留的 `.prepare-*` 目录需要在确认没有活跃准备任务后清理。

## 状态、证据与恢复

接口为 `tool_request`、`tool_status`、`tool_exec`，状态同步写入 RunState 的 `tool_environments`。CLI/TUI 显示准备过程，右侧解题进展显示最新能力状态；上下文刷新和压缩后仍能恢复环境 ID。

记录 wheel 锁、安装命令和日志、image ID、输入哈希、执行命令、退出状态、输出证据与产物来源。工具安装成功只表示能力可用，不是解题假设成立。

临时容器启动前记录拥有关系，清理完成后移除活动记录，`recover` 可清理中断时遗留的工具容器。停止运行会取消准备任务、等待清理，不重启主沙箱来安装工具。

`replay` 支持 `tool_exec` 及输入/脚本/产物 ID 映射，只允许当前镜像已有能力或本地经过验证的扩展缓存；缓存缺失或损坏时明确失败，不静默跳过，也不在回放时下载。

## 保留的能力边界

GUI 启动不等于有交互桌面或无人值守 API。设备、Android 动态分析、SDR、服务连接、许可证和其他精确版本编译器仍有各自条件；供应接口不扩大原题网络授权，也不挂载宿主显示、设备或 Docker socket。大型原生工具缺失时报告阻塞，不触发隐式的大型下载。
