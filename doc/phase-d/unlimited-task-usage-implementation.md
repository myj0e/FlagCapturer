# 取消任务额度与 Token 用量展示

日期：2026-10-09

## 行为

任务不再设置模型轮次、总运行时长、token 或累计工具输出额度。`AgentLoop` 持续执行，直到模型显式 `run_complete`、用户 Stop / Ctrl+C，或者实际 provider / runtime 错误导致无法继续。没有工具调用的一轮也会续轮，不再被当作任务完成。取消整次任务与 provider 长轮的默认固定超时，等待期间仍可取消。

保留用户确认的单次工具保护：普通命令最多 120 秒、后台脚本最多 1800 秒、单次模型侧工具回复最多 16 KiB，以及各读取/会话的单次大小与超时保护。工具超时或截断仅影响该次操作，允许继续读取证据、恢复计算或执行下一步。初始化握手、服务就绪、清理和离线命令回放各自的操作超时继续有效。网络授权窗口继续按授权约束执行。

TUI / solve / evaluate 均使用无任务额度的共享服务。移除 `RunLimits.max_turns` / `wall_time_seconds`、solve 的 `--max-turns` / `--wall-time`、evaluate 的 `--max-total-turns` / `--max-total-wall-time`，评测不再按轮次或时间额度跳过后续任务。运行元数据记录 `automatic_budget_stop=False`，界面仅显示实际轮次，不显示轮次额度分母。

## 用量统计与 statusline

底部示例：`Token 1.2M（入 1.1M / 出 100K） · 上下文≈60K / 200K (30%)`。

- 小于 1000 显示整数；自动按 K=1000、M=1000000 缩放，最多一位小数。
- Token 是本次任务累计用量，上下文是窗口占用，两者独立展示。最新推理用量仅作上下文估计，使用 ≈ 标记；缺少数据时显示 `—`。
- provider 用量事件立即写入证据并送到 UI，不等下次工具调用或轮次结束。按 provider thread 的累计快照差值计费，重复/倒退快照不重复相加，不同 thread 的差值可合并；不把缓存 token 再次加进总量。
- provider 累计用量优先，未提供时使用轮次 usage 的累计值；不同时相加两路数据。完成结果与事件证据保留累计统计。上下文压缩或超过窗口显示值不会清零累计 token，也不会触发任务额度终止。
- 每次新任务重置统计。Flag 弹窗和历史手动跟踪行为保持，token 更新不刷运行记录、不改变阅读位置。

旧日志和旧本地服务验收回执保持可读；旧回执中的总任务超时用例只作历史兼容，新验收对应单次服务启动超时。合成 provider 的脚本用完但未显式完成会报告 fixture 错误，避免测试数据默默无限空转或假造完成。

## 最终验收

开发阶段未运行 smoke 测试，所有功能与用例修改完成后统一验收。验收使用合成 provider、模拟运行时与剪贴板，不调用真实模型。

验收范围包括：超过原 30 轮和 1800 秒后继续；连续无工具轮次继续；超过窗口显示占用和百万 token 时仍处于活动状态，直到手动 Stop；输入/输出/总量单位缩放；重复与跨 thread 快照统计；压缩后累计用量保留；单次工具超时后继续；多 Flag 实时窗口、历史跟踪和取消清理回归。

最终验收命令：

```bash
.venv/bin/python -m pytest -q --deselect tests/test_stage_a_runner.py::OfflineAcceptanceTests::test_synthetic_agent_loop_completes_without_provider_or_docker
```

最终结果：**270 passed, 1 deselected**（59.05 秒）。排除的是调用 `run_synthetic_smoke` 的既有用例，未执行 smoke 命令或真实模型验证。首次验收发现两个旧单文件 fixture 缺少显式完成调用、一个候选 UI 事件同步条件过早，修正后整体复验通过。`git diff --check` 通过。
