# 无已知答案的候选流程

正常解题不要求已知 flag。模型使用附件、用户提示及运行证据选出候选，原文保存在私有 evidence 中；最终由用户在比赛平台确认。

## 当前流程

1. Preview 检查附件哈希、运行镜像、预算及模型传输授权。
2. `candidate_submit` 记录候选及来源，不进行格式或已知答案匹配，也不自动结束。
3. `candidate_check` 可检查特定局部关系。通过仅表示该关系成立，不确认 flag 正确。
4. `run_complete` 以 `candidate_unverified` 或 `unsolved` 结束。前者必须引用本次运行中记录的候选 ID。
5. TUI 的 Flag 窗口默认选择最新候选，可查看、切换并复制原文。报告只引用私有证据，不写入候选原文。

CLI 示例：

```sh
ctfbot solve --workspace data/imports/<snapshot>/workspace \
  --runtime-image sha256:<image-id> --runs-root runs/local \
  --additional-prompt 'Flag 前缀可能是 CTF' --confirm-model-usage
```

`candidate_unverified` 的退出码为 0 表示尝试完成并保留候选，不表示答案正确；未解出、预算耗尽或运行错误返回 1。

## 接口与数据迁移

- 删除 CLI 的 `--oracle` 和 TUI 答案路径输入，删除已知答案验证模块及自动 `verified`/`rejected` 分支。
- `LocalChallengeService.preview/run` 接收 `workspace, runtime_image, runs_root, limits`；`validate_baseline_snapshot(workspace)` 返回 workspace、provenance、task。
- `ToolRegistry` 不再接收答案路径，`RunResult` 不再提供 `verified` 字段。查看 `status` 和 `candidate_ids`。
- `import_service_fixture`、`import_remote_fixture` 返回 workspace 路径，不生成额外答案文件。
- 数据集 schema 升为 v3，每项包含 challenge_id、workspace、category、labels、provenance_sha256、exposure、contamination、mechanism。旧 v2 清单被拒绝；可重新运行 `ctfbot fixtures`，自定义清单需移除答案路径并改为 v3。
- `evaluate` 保留预算、holdout 隔离、授权、执行条件冻结及失败统计。`completed_with_candidate_over_scheduled` 表示完成并保留候选的运行比例，不是正确率。候选数量、局部检查和运行错误分别记录。
- 重复的 Stage A `baseline` 批处理和其 runner 已删除，批量运行统一使用 `evaluate`。旧 pilot 答案导入/审查/验证脚本及 direct-tool-loop 原型已退役；历史阶段文档中的命令仅供追溯。
- 服务与远端验收中的 solve 状态改为 `candidate_unverified`；网络隔离、授权、健康检查和资源清理要求保留。旧验收记录不能直接替代当前流程的验收。

## 开发验证

```sh
. .venv/bin/activate
ctfbot baseline-smoke
python -m pytest -q
python doc/phase-d/verify_unknown_flag.py --single-file --output data/acceptance/<new-dir>
```

最后一项使用真实 Docker、自建附件和确定性 provider，验证 TUI 单文件导入、候选结束、报告与回放，不调用真实模型。六类开发/holdout 的同类验收可运行 `doc/phase-d/verify_workflows.py`。

云平台没有暴露 `/proc/<pid>/task/<pid>/children` 时，现有分离子进程清理测试会失败；该限制与答案流程移除无关，不能据此声称分离后台子进程清理已验证。
