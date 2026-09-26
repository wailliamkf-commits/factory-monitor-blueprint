# 快速开始：先跑可验证的合成实验

这条路径不请求屏幕权限、不启动模型、不读取真实摄像头、不点击桌面。它验证模块逻辑，不能产生真实行为识别能力。

## 1. 取得固定版本

从本仓库 Releases 下载 `FactoryMonitor-Blueprint-v0.2.0-preview.zip` 和 `SHA256SUMS.txt`，核对 ZIP 哈希后解压到新的本地目录。仓库当前公开；下载需要可访问 GitHub，不能把下载成功推定为大陆现场可访问。也可由开发机下载后通过获准介质转移，见[离线交接](09-delivery-and-offline.md)。桌面新入口与旧完整资源包的版本关系见[桌面执行说明](12-demonstration-and-field-run.md)。

以下所有命令默认在解压后的**仓库根目录**执行。先确认这里有 `README.md`、`implementation/` 和 `scripts/`。不要在 ZIP 预览窗口中运行。

## 2. 选择一种 Python 环境

### 已有此前准备好的项目环境

Windows 双击 `implementation\scripts\run-active-observation-lab.cmd`。弹出选择窗口时选既有候选项目的 `.venv\Scripts\python.exe`。它不安装依赖，要求 Python 3.12、NumPy 与 OpenCV。

PowerShell 可明确指定路径（替换示例路径）：

```powershell
& 'D:\你的候选项目\.venv\Scripts\python.exe' scripts\verify_repository.py
& 'D:\你的候选项目\.venv\Scripts\python.exe' implementation\scripts\experiment_active_observation.py --output runs\active-observation
```

Mac 使用既有项目环境的绝对路径：

```sh
/你的候选项目/.venv/bin/python scripts/verify_repository.py
/你的候选项目/.venv/bin/python implementation/scripts/experiment_active_observation.py --output runs/active-observation
```

### 没有现成环境，当前电脑可以联网

仅做这个实验无需 PySide6、YOLO、Ollama 或显卡。以下会在仓库根目录创建 `.venv` 并联网安装两个依赖，不安装完整监控应用。

Windows PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install 'numpy>=1.26,<3' 'opencv-python>=4.10,<5'
.\.venv\Scripts\python.exe scripts\verify_repository.py
.\.venv\Scripts\python.exe implementation\scripts\experiment_active_observation.py --output runs\active-observation
```

Mac 终端（已安装 Python 3.12）：

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install 'numpy>=1.26,<3' 'opencv-python>=4.10,<5'
.venv/bin/python scripts/verify_repository.py
.venv/bin/python implementation/scripts/experiment_active_observation.py --output runs/active-observation
```

两个依赖范围来自固定源码；上述便捷安装不是锁定离线环境或已验证的 4060 配置。网络受限时使用获准的本地资源，按[离线交接](09-delivery-and-offline.md)准备；本流程不会自动选择镜像或反复换源。

## 3. 查看实际输出

命令行方式输出到 `runs/active-observation/`；双击原启动器则输出到 `implementation/results/`。生成 `experiment.json` 和 `结果说明.md`。

合格的这次实验应显示：

- `experiment_gate: PASS`，七项 checks 为 true。
- `field_gate: NOT_TESTED`，`automatic_control_authorized: false`。
- 16 路的首轮巡视记录、无人像矩形变化和错误回执故障记录。
- 串行单屏 16 路的盲时预算计算为 FAIL；这项 FAIL 是被正确发现的不可行假设，不是脚本运行失败。

`source` 必须为 `synthetic`。CPU 时间只是小图场景观察器的计算耗时，不含采集、YOLO、Qwen、录像或真实网络。

## 4. 遇到失败

|现象|先检查|下一步|
|---|---|---|
|Python 找不到|是否安装 3.12，选择的是否 `.venv` 解释器|纠正解释器路径，不修改系统默认 Python|
|缺 NumPy/OpenCV|是否选错已有环境|在可联网准备机补依赖，或采用已校验的离线资源|
|源文件校验失败|是否改动或缺失 `implementation/` 文件|保留错误，重新解压到新目录核对；不覆盖现场修复版|
|实验 FAIL/退出码非零|JSON 的失败项与原始报错|保存输出，按[故障处理](06-operations-and-recovery.md)诊断|

实验通过后再选择 [Windows 完整执行](02-windows-execution.md) 或 [Mac 完整执行](03-macos-execution.md)。合成实验不要求真实画面，也不代表这些后续步骤已经完成。
