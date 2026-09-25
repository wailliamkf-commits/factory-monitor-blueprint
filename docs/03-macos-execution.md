# macOS 执行手册

macOS 是辅助开发与平台验证路线。Windows 仍是优先现场平台；Mac 上的构建、合成演示或捕获预览不能代替 Windows/WGC/RTX 4060 证据。目前没有经过验收的 macOS Seetong 十路运行结果，也没有目标 Mac 上的逐路身份、模型准确率或长时间稳定性证据。

## 适用条件与边界

- Python 3.12；桌面功能依赖 PySide6，视觉检测依赖 Ultralytics 与相关组件。
- 实时窗口采集走 ScreenCaptureKit。首次调用会用 Xcode 工具链编译包内的 Swift helper，并缓存到 `~/Library/Caches/FactoryMonitor/`。需要 Xcode Command Line Tools、系统 ScreenCaptureKit 框架和操作员在系统设置授予屏幕录制权限。
- 权限弹窗、缓存 helper 编译成功或应用窗口可见都不证明已捕获到目标客户端。需由操作员选中目标窗口，查看实际帧、尺寸、遮挡/最小化时行为和退出状态。
- 当前 Seetong 的 Windows 安装文件为 Windows 格式。Mac App Store 页面注明该版本为 iPad 设计且未针对 macOS 验证；Mac 芯片/系统满足页面所列条件也不能保证十路布局、ScreenCaptureKit 或稳定性。由现场人员先确认受支持客户端及授权画面；不要将兼容层/虚拟机当作 Windows 验收。
- Mac 端没有 6.6 GB Windows 离线候选包的安装路径。该包中的 CUDA 12.8 wheels、Windows Ollama 和 Windows WGC 依赖不能用于 Mac。本仓 Release 提供完整 Windows 资源，模型权重可复用，但未提供 Mac 全离线安装器，不能在 Mac 离线一键运行。

## 在线创建项目环境

在终端进入新仓库的 `implementation/` 目录。若机器尚未安装 Xcode Command Line Tools，执行安装请求并按系统提示完成；若命令报告已安装则无需重复处理。以下 Python/PyPI 安装要求依赖网络或组织配置的可用包源：

```sh
cd implementation
xcode-select --install
python3.12 --version
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev,desktop,vision]'
.venv/bin/python -m factory_monitor init --config config.json
.venv/bin/python -m factory_monitor preflight --config config.json --data-dir data
```

如果 Xcode 工具链尚未可用，先完成系统安装；不要改用截图轮询来冒充 ScreenCaptureKit。依赖安装报错时保留错误并确认 Python 3.12 架构、可用包源及磁盘状态。首次创建的 `.venv` 仅属于 `implementation/`；若配置文件已存在，`init` 会拒绝覆盖，应检查并备份既有配置。`preflight` 不请求屏幕权限、不启动模型或抓取实际窗口。

上述步骤的预期结果是项目内 `.venv/`、未校准的 `config.json` 和本地诊断输出。它没有安装 Seetong、下载模型或创建 Windows 便携包。

## 合成启动与本地模型

在 `implementation/` 目录启动：

```sh
./scripts/launch-factory-monitor.command
```

窗口启动时默认 `demo`。合成标签必须保留；它只用于熟悉操作界面，不能记录为真实画面或现场成功。命令若提示缺 `.venv`，返回环境准备步骤核对，不要从其他电脑复制虚拟环境。

如果已经按授权流程准备本机兼容的 Ollama 可执行文件 `.tools/ollama/ollama` 以及模型目录 `models/ollama/`，另开终端运行：

```sh
./scripts/start-local-model.command
```

服务只监听 `127.0.0.1:11435`，并设置 `OLLAMA_NO_CLOUD=1`；终端需保持打开。脚本只检查本地文件，不安装工具、不下载模型。缺少文件时先停止，不要把 Windows 二进制文件搬到 Mac，也不要开启外部云端分析。

配置的 `cloud_enabled` 默认关闭。启动 GUI 后检查本地 endpoint 与模型名称；既有配置不会被启动器改写。模型能连接只证明接口可达，并不证明图像语义正确、十个事件在 15 秒内完成或可在现场采用。相关负例及突发结果见[大陆执行记录](../implementation/docs/MAINLAND_EXECUTION_20260925_zh.md)。

## 实时检测模型（必需）

Mac 上启动 `live` 前，必须先把获准且哈希核实的 YOLO 权重放入 `implementation/models/yolo11n.pt`。运行时所有非 `demo` 来源都会构建 YOLO 检测器；缺少该文件会导致检测 worker 无法启动。可复用经批准的相同 `yolo11n.pt` 权重文件，但要按[冻结来源的 Windows 交接](../implementation/docs/WINDOWS_HANDOFF_zh.md)核对该文件的 SHA-256 与许可。只复用模型权重本身，不复制 Windows Ollama 二进制、CUDA wheels 或 WGC 组件。

```sh
mkdir -p models
shasum -a 256 models/yolo11n.pt
```

将输出与冻结交接文档列出的期望 SHA-256 逐字符比较；缺文件或哈希不符时停止，按获准来源重新取得，不能运行 live。合成 `demo` 不需要权重。

Qwen/Ollama 仅是候选之后的可选本地复核。若未准备 Mac 对应的本机 Ollama 程序和模型，在新建配置中将 `review.enabled` 设为 `false`，事件会进入人工复核流程；GUI 没有该开关，维护人员须通过项目配置 API 修改并读回配置：

```sh
.venv/bin/python -c "from pathlib import Path; from factory_monitor.config import load_config, save_config; p=Path('config.json'); c=load_config(p); c['review']['enabled']=False; save_config(c,p); print(load_config(p)['review']['enabled'])"
```

输出应为 `False`。不要用 Windows 程序或 CUDA wheel 填补这一可选复核项。

## ScreenCaptureKit 权限与首轮捕获

先让受支持的监控客户端在本机正常显示获授权画面。在应用设置中选 `screencapturekit`，输入客户端的精确窗口标题与实际捕获像素尺寸；核对布局版本与每路画面位置。勾选「我已逐项确认十个摄像头 ID、布局和目标窗口/显示器；保存此布局为本地校准」，按「验证并原子保存配置」，退出再重开并读回。窗口标题、后端、尺寸或裁剪改变会令相关校准失效。

首次启动 live 可能触发 Screen Recording 权限请求。由操作员在 macOS「系统设置 → 隐私与安全性 → 屏幕与系统音频录制」（不同系统版本名称可能略有差异）授权当前启动应用/终端所属执行程序。系统通常需要重启应用才会应用新权限。只授权实际需要的本地应用；无权限、helper 编译失败或窗口不存在时停止，读取本地错误并修复权限/工具链，不反复改全局安全设置。

启动时选 `live`，确认正在显示的是准确窗口和新鲜画面。若用 `display`，这代表操作员明确选择整个显示器，必须注明会捕获同屏其他窗口的风险；它不是 ScreenCaptureKit 的窗口身份验证。遮挡、最小化、切换空间、休眠/唤醒和屏幕锁定时的行为均需现场观察。任一情形下画面停滞或身份不明，即停止该路并标记不可观测。

应用可以提示相机视角或显示「返回九宫格/网格」请求，但自动 OS 点击在目前版本始终禁用。操作员须在监控客户端手动切换，检查新的捕获时间及身份。配置文件或 profile receipt 只是本地声明/数据检查，不给予桌面控制权。

## 停止、产物与回退

按应用的「停止」并等待状态显示已停止。保留配置、终端错误、本地报告和事件数据路径；实际监控图像和录像只保留于受控本地，不放入本仓。若捕获 helper 或子进程没有退出，标记该次结果不完整，不要立即重开同一数据库或覆盖输出；先核对进程和 SQLite 附属文件。

如要打包本地 wheel/sdist，在 `implementation/` 执行 `./scripts/build-macos.command`。预期在 `dist/` 产生 Python 分发文件；这不是 `.app`、安装器、Windows 构建或现场验收结果。打包失败时保留输出，在隔离目录修复构建环境。

回退采用新目录方式：停掉本地服务与应用，保存本次数据后隔离新环境，恢复上个已知源码目录与兼容配置副本。先检查再移除该项目自己的 `.venv/`；不要删除用户级权限、helper 缓存或其他程序资产来掩盖失败。若目标窗口、权限或捕获行为与预期不同，停止校准并保留现场情况供实施人员判读。
