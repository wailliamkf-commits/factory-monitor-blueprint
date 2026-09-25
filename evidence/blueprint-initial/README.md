# 蓝图仓库首次本机验证

在新仓库的 `implementation/` 副本执行，而不是复用旧目录的测试输出。

- 开发 Mac / Python3.12 既有依赖环境，全套274 passed / 1 skipped；跳过的是未随仓库打包的公开YOLO模型样本。
- 使用 offscreen Qt；该开发机需既有未隐藏的Qt插件副本。此环境条件不进入可移植安装成功声明。
- 新副本主动观察合成实验7项通过，field_gate仍为NOT_TESTED。
- 仓库校验比对129个冻结文件，并检查当前手册相对链接和模板JSON。发布前重新执行。

实际控制台输出见 `pytest.log`，实验原始轨迹见 `experiment.json`。新GitHub提交另跑Windows/Mac CI，并在对应Release记录链接；模型/目标Windows/真实十路仍未验收。

[离线资源 ZIP 检查](offline-zip-verification.json)：本机对固定原 ZIP 的整体哈希、107 个 payload、6 个模型文件和 5 个 Qwen manifest 引用做只读核验。没有执行 Windows 安装或现场推理；发布后下载回读另记在 Release 验证报告。

[固定 ZIP 配方本机验证](rebuild-recipe-local-verification.json)：用原有本地 payload 与精确保留的 ZIP 头/索引重新计算，整体及 7 片哈希均与原包一致。此记录没有执行远端资源下载；云端抓取与上传另外以对应工作流及 Release 回读记录为证。
