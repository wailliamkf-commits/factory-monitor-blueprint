# 冻结源版本验证记录

对应原项目提交 `260862912d7386642aa538ac49d72442b9d673bb`，不是本仓库新增发布的现场验收。

- `experiment.json`、`结果说明.md`：Apple M5 开发机合成实验，7项通过，现场NOT_TESTED。
- `full-pytest.log`：该开发机275项测试通过。
- `ci-final.json`、两份`ci-*.log`：Windows 272 passed / 3 skipped、Mac 274 passed / 1 skipped；云端十路合成检查、主动观察实验与构建通过。
- 原CI：[双平台记录](https://github.com/wailliamkf-commits/factory-monitor/actions/runs/36091225947)。

本仓库当前提交另有独立CI；原始结果不能自动用于新改动或真实监控画面。没有生产图像、模型权重或现场账号。
