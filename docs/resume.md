# 任务续接与产物生成路径

工作台 `/resume` 选择记录，或 `/resume <run-id|run-directory>` 直接续接。CLI：

```sh
node src/cli.js resume runs/<run-id> --confirm-model-usage
```

CLI 默认沿用原 provider、模型和思考强度，也允许显式覆盖。工作台使用当前模型，没有选择时使用原模型。恢复使用原来的不可变本地镜像 ID，不拉取、不切换镜像。每次续接创建新目录，保留原记录，关联 task_id 和 parent_run_id。已完成任务不能直接续接；停止、失败、partial/unsolved 可以续接。recover 仍只清理残留容器，清理未确认时必须先 recover。

## 生成关系

原始附件是根资产。派生产物记录父资产 ID、输出哈希、保存字节及实际执行 ID，形成依赖图。日志、观察和失败尝试只保存来源，不强行视作可恢复文件。

- command_run/script_run 接受 input_asset_ids 和 outputs（path、可选 display_name），成功后自动保存输出并关联实际执行。执行记录包含 argv、镜像、原始请求、退出状态、stdout/stderr 和所用脚本源码引用。
- workflow_run/tool_exec 自动关联输入、执行和捕获输出；内置 workflow 程序也保存源码版本。
- 后台 script_start/session_start 返回 execution_id，但启动不代表成功。后台产物通过 asset_capture 显式保存。
- asset_capture/artifact_export 可传 execution_id，人工指定的生产关联标为 model_declared。未关联执行的资产仍能恢复字节，但生产者为 unknown。
- 资产 materialize 记录路径，使之前传给脚本的路径在续接后仍可用。

输入依赖是声明，不证明命令没有读取其他文件；工具不会猜测任意 shell/Python 的文件读写关系。执行成功、保存字节和语义正确是不同概念。

## 总结与压缩

每十轮总结和 Pi 压缩共用 ContextMemory。总结模型在 references 中选择下一步需要的文件，并区分事实、假设、失败范围和下一步，不编写恢复命令。程序沿父资产计算、校验 recovery_plan，保存到 context_snapshots。完整操作在 executions/assets 中按 ID 查询，不重复塞进上下文。

停止时不额外调用模型。stop_checkpoint 包含最后总结选择的资产及依赖，也纳入总结后生成的资产；近期交互由 Pi 会话保留。未保存的临时文件不能凭总结恢复。

## 恢复机制

1. 校验任务、附件、事件顺序及镜像，拒绝已完成或清理未确认的记录。
2. 继承证据和 RunState，保持记录 ID，使新目录不依赖父目录存活。
3. 使用 Pi SessionManager.forkFrom 继承原生会话和压缩记录，保留模型协议数据。停止时保存带哈希的会话 checkpoint；旧记录兼容读取唯一的原生 session 文件。未产生交互的停止记录允许新建会话。
4. 在原镜像的新容器中，还原选中产物、历史 materialize 路径和已保存脚本。同一路径的多个版本只将最新版本放回原路径，旧版本仍可按资产 ID 读取。冲突、缺失明确报告。
5. 旧工具环境标为 interrupted，通过 tool_request 重新检查镜像与缓存。后台进程和交互会话不恢复，不自动重放命令、扫描或网络操作。
6. 给 agent 注入实际恢复结果、缺失项和完整 recovery_plans 查询入口，继续解题。

网络授权不从历史记录继承：CLI 显式提供 network-grant，工作台重新校验当前设置的授权及有效期。恢复只保证文件字节，不恢复进程、描述符或原执行权限。未导出文件、超过资产上限的文件，以及强制杀进程前未落盘的数据不承诺恢复。

Pi 恢复有效历史供模型继续推理，不恢复服务端中断瞬间尚未返回的内部计算。

远端 Web/Pwn 续接以有效历史路线引导 agent 主动重建进度。总结中的 recovery_steps 保存前提、动作、预期观察、易变状态、证据与脚本/资产引用。新连接重新获取登录、菜单、ASLR/fd 等状态；偏差退回最近可验证节点，不机械重放修改服务的动作。网络授权按当前 attempt 重新确认，Cookie/profile/凭据不作为恢复资产。详见 [remote-solving.md](remote-solving.md)。
