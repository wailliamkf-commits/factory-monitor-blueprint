# Windows 执行手册

本手册面向目标 Windows 电脑上的操作员。Windows 是当前优先验证平台；当前总体验收仍为 **FAIL / 未通过**。没有目标 Windows 远程访问权限，因此本手册只描述待现场执行的步骤，不声称目标机、RTX 4060、Seetong、十路并发或模型准确率已验证。

## 两种准备路径

### 新电脑：在线安装本仓源码

本仓源码位于 `implementation/`。此路径需要 Windows 上能从配置的 Python 包源下载依赖。本仓源码 ZIP/Git 只含源码和文档，**不含约 6.6 GB 离线候选包里的大型安装器、Ollama 运行文件、模型权重或 wheels**；源码 ZIP 不能离线一键运行，也不会自动下载模型。此路径先只装项目依赖和运行合成演示。同一仓库的 Release 另提供完整资源包的 7 个分片与无 Python 还原工具，见[完整资源下载与还原](09-delivery-and-offline.md)。

### 已有电脑：保留既有 6.6 GB 离线候选包

如果现场电脑已有该候选包，应保持原目录、现场修复副本、配置、模型、数据库和证据不动，并沿用该包随附的版本说明与清单。本仓源码不等同于该包。冻结源码中 `implementation/scripts/windows/Prepare-Candidate.ps1` 需要同一包目录下的 `requirements-windows.lock`、`changes.patch`、`resources/`、离线 wheelhouse、便携 Git 和模型；这些大体积资源不在本仓。**不要从本仓源码 ZIP 运行它并将失败归因于现场环境，也不要用新源码覆盖正在运行或含本地修复的目录。**

该候选包的准备脚本设计为从现场最新修复源码复制 `src/`、`tests/`、`scripts/` 与允许的根文件到全新目录，再应用带上下文检查的补丁；补丁不匹配时应停止并保留原源码。候选包成功创建也只代表隔离候选完成依赖安装和定向合成检查，不代表真实监控验收。

包内 `offline_bundle_manifest.json` 可核对文件哈希；已交付包记录为 6,637,814,564 字节，外层 SHA-256 `4652f9677df0e97f48be8b3da82d7a9db7b07a3b1bdcdeb70e1497a2fa92a4a1`。现场需对收到的实际文件重新计算并比对同一交付记录，不以文件名相同推断一致。将整个包复制到 Windows 本地磁盘（不要从压缩包预览中运行）；解压、候选、模型和证据还需空间，交付说明建议至少预留 50 GB。缺 Python 3.12.10 x64 时，包内含离线安装器；安装时包含 Python Launcher。Microsoft Visual C++ x64 运行库缺失时，随包提供安装器，但是否安装由现场错误与既有 IT 策略决定，不要无故更改驱动或系统安全设置。

在离线包根目录按编号依次运行随包入口：

1. `1-prepare.cmd`：按提示提供现场**最新修复项目**目录。预期在其旁边创建新候选目录、核对整包哈希、从随包 wheelhouse 安装并运行定向检查；源项目不应变化。补丁上下文不匹配或安装失败时，保留错误和新候选目录，停止升级，不重试覆盖现场。
2. `2-model.cmd`：输入准备好的候选目录，启动随包本地 Qwen 服务，仅监听 `127.0.0.1:11435` 并关闭云功能。保持窗口开启。端口已被占用时脚本停止且不结束现有进程；先查明监听者。
3. `3-preflight.cmd`：在候选目录保存 `preflight.json` 和 `gpu.json`。这是本机就绪与 GPU 探针，不是容量或现场验收。
4. `4-capacity.cmd`：执行 CUDA 检查、公开测试图上的检测与模型复核组件基准。它不读取摄像头；负例误报或期限失败须按 FAIL 保留。
5. `5-monitor.cmd`：启动候选界面。操作员逐步完成下文校准，再自行选 live 并按「开始」。
6. `6-collect.cmd`：将候选报告、日志和源指纹打成本地诊断 ZIP；不会自动发送。生产配置、摄像头图像和录像不随诊断包收集。

预期报告位于候选目录的 `reports/local/windows-validation/`；第 6 步生成带时间戳的本地 ZIP。每一步失败都保留终端文字、候选目录和包清单，先定位当前失败再继续。包内公开图片基准、Mac 演练和 CI 记录不能替代目标 Windows、4060 或真实十路证据。离线包里的 `candidate-source.zip` 只用于对照/审阅，不能覆盖现场修复源码，也不可对已含补丁的源码重复应用补丁。

## 新电脑在线准备

在 Windows 安装 Python 3.12 x64（带 Python Launcher）与 ffmpeg。打开 PowerShell 并先进入新仓库根目录；以下第一条命令切换到 `implementation/`，此后的命令均以此为当前目录：

```powershell
Set-Location -LiteralPath .\implementation
py -3.12 --version
ffmpeg -version
powershell -ExecutionPolicy Bypass -File .\scripts\setup-windows.ps1
```

预期：`py` 显示 Python 3.12；安装脚本在 `implementation/.venv` 建立或复用项目专属环境，在线安装 `dev`、`desktop`、`vision`、`windows` 依赖，运行依赖一致性与模块导入检查，最后提示未建立配置、未启动采集、未下载模型。ffmpeg 不在 PATH 时会警告；它不会因此改动全局 PATH。

安装脚本只操作此项目的 `.venv`。若 `.venv` 存在但不完整或不是 Python 3.12，脚本会停止。先检查它确实是本目录内可丢弃的环境，再由维护人员手动移除 `implementation/.venv` 后重跑；不要清除其他目录。网络、代理或包源失败时，保留错误并确认允许的包源后重试。这个在线安装脚本不是离线安装器。

建立一次性配置并做不读屏幕的预检：

```powershell
.\.venv\Scripts\python.exe -m factory_monitor init --config config.json
.\.venv\Scripts\python.exe -m factory_monitor preflight --config config.json --data-dir data
```

`init` 拒绝覆盖已存在的 `config.json`。预期产物为未校准的 `config.json`、本地 `data/`（首次执行相关功能时建立）以及终端中的预检结果。预检不请求桌面权限、不选窗口、不启动模型，也不改系统设置。若配置已存在，先备份并检查，不要删除或重建以掩盖错误。

用启动器打开应用时，默认来源是合成 `demo`：

```powershell
.\scripts\launch-factory-monitor.ps1
```

应用标示 `SYNTHETIC / DEMO`。只有在操作员于应用中选择来源并按「开始」后才会执行相应来源。合成演示可用于熟悉画面与本地事件记录，不读取 Seetong，也不能证明相机身份、准确率或十路性能。需要单独的 30 秒演示报告时：

```powershell
.\.venv\Scripts\python.exe -m factory_monitor demo --config config.json --data-dir data --seconds 30 --report reports\local\demo.json
```

预期产物是 `reports/local/demo.json`。命令失败时保留输出与退出码；不要把合成数据放进现场验收样本。

## 实时检测模型（必需）

`live` 实时运行必需有 `implementation/models/yolo11n.pt`，并且文件哈希已按维护流程核实；缺少它时检测 worker 无法启动。在线源码安装脚本和 Mac 构建都不会下载权重。Windows 离线候选包自带已清单校验的 YOLO 权重；新电脑在线路径需由维护人员按授权来源另行放入 `models/yolo11n.pt`。官方来源、SHA-256 和手动准备步骤见[冻结来源的 Windows 交接](../implementation/docs/WINDOWS_HANDOFF_zh.md)。中国大陆环境若来源不可达，停止并使用已审核的离线交付来源，不要降低哈希核对要求。合成 `demo` 无需此权重。

## 本地视觉复核（可选）

Qwen/Ollama 只提供候选之后的本地视觉复核，不是捕获或 YOLO 检测的替代。若不准备该功能，将 `config.json` 中的 `review.enabled` 设为 `false`；此时事件需人工复核。GUI 没有该开关，维护人员可在 `implementation/` 目录通过项目配置 API 修改并读回确认：

```powershell
.\.venv\Scripts\python.exe -c "from pathlib import Path; from factory_monitor.config import load_config, save_config; p=Path('config.json'); c=load_config(p); c['review']['enabled']=False; save_config(c,p); print(load_config(p)['review']['enabled'])"
```

输出应为 `False`。

若启用本地复核，维护人员依授权与来源流程另行准备 Windows 对应 Ollama 完整发行文件与 `models/ollama/`。不得从其他操作系统复制二进制文件，不要只复制 Ollama 主程序而遗漏依赖 DLL。工具和模型均留在本机项目目录。

在 Ollama 程序和模型都已位于本项目目录后，另开 PowerShell，在 `implementation/` 目录运行：

```powershell
.\scripts\start-local-model.ps1
```

预期只监听 `127.0.0.1:11435`，终端窗口需保持打开。它不下载模型，云端功能由 `OLLAMA_NO_CLOUD=1` 关闭。若端口或模型错误，保留终端输出，检查本地路径及已有端口占用；不要终止未知进程，也不要将服务绑定到局域网。

`init` 的默认 review endpoint 为本地 11435，`cloud_enabled` 默认关闭；确认配置读回值与模型名称后再做本地复核。模型单次返回或 JSON 格式正确不代表判断正确。现有十件并发复核 15 秒期限与负例语义已有失败记录，详见[大陆执行与架构验证](../implementation/docs/MAINLAND_EXECUTION_20260925_zh.md)。

## 目标 Windows 的首次人工操作

开始前先确认：

- 操作员能在 Seetong 中正常观看获授权的目标画面，并记录 Seetong 版本、窗口模式、Windows 显示缩放比例和屏幕分辨率。
- 项目在新的隔离目录中；不沿用未知 `.venv`、`config.json`、数据库、录像或模型。
- 已按下一章完成并读回本机校准；只做 1 路试跑，有明确停止方法。
- 本地提醒及证据写入位置有足够磁盘空间。初始证据保留/并发配置不是经长期现场负载认证的容量保证。

打开应用后在来源下拉框选 `live`。在设置区选择 Windows 后端 `wgc`，填写客户端的精确窗口标题、实际捕获尺寸，逐项核对十路 ID、网格顺序及裁剪，勾选「我已逐项确认……」后按「验证并原子保存配置」。退出、重启并读回 `config.json`。应用启动器本身固定进入 demo；启动 live 前必须在 UI 中明确选择 `live`。不要使用全屏 `display` 作为 WGC 的替代品，除非另有明确记录其只捕获显示器以及包含的其他窗口风险。

先让一个已校准来源启动，核对捕获窗口和画面变化，再停止并检查本地健康状态。按 1、3、10 路逐级进行，每级保留开始/结束时间、各路新鲜度、相机标识、候选、超时、证据缺口和操作员标注。任何错绑、停滞、遮挡、缺帧、超时或无法停止，立即停止本级并保存记录，不扩大路数。运行时每路心跳需独立；客户端公共时钟在跳不代表所有摄像头都更新。

应用有「开始」「停止」「返回九宫格/网格」「人工确认」「标记误报」等控件。后两者是人工标记。返回网格控件可提出请求，但目前 OS 点击始终禁用；操作员需亲自在 Seetong 切换视图。不要把控件存在、profile receipt、画面中有游标或 `pyautogui` 已安装视为操作系统控制能力。主动观察的四个模块仍是隔离实验，没有接入 runtime。详见[主动观察架构及核验状态](../implementation/docs/ACTIVE_OBSERVATION_ARCHITECTURE_20260925_zh.md)。

## 停止与回退

先在应用中按「停止」，等状态显示已停止并记录 worker 退出、最后队列计数与输出文件。若停止卡住或进程仍留存，记为停止失败；不要立即重启去覆盖 SQLite/WAL 或正在写入的媒体。若无法自然退出，现场负责人应保存进程、日志与时间，再按既有 Windows 作业程序处置，该次数据标为不完整/未知。

更新时使用新版本目录和独立数据根目录。先核对旧版本、修改、配置、数据库及模型的身份，备份一致状态后才验证新版本。失败时隔离新目录，恢复上一版本及与它兼容的数据副本；不要将旧数据库覆盖已有新写入的数据。卸载/回退只移除确认属于本项目的 `.venv` 或候选副本，先保全证据与配置。完整停止和更新约束见[稳定运行与版本交付](../implementation/docs/STABLE_OPERATION_zh.md)。
