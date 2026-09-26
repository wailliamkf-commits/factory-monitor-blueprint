# 桌面演示与目标设备现场运行

这份手册先建立可重复的本机合成演示，再规定目标 Windows 现场怎样逐级验收。合成演示运行真实 PySide6 桌面窗口和真实 Runtime 的合成输入，但脚本注入部分操作、不调用视觉模型，也不访问 Seetong。`--render-recording` 可保存实际 Qt widget 绘制画面的合成录制；它不是操作系统屏幕录制，也不能证明客户端适配、真实画面准确率、模型有效性或多路性能。

## 可复现的桌面合成演示

### 准备

1. 从仓库 Release 解压完整 Blueprint ZIP 到本机磁盘，并在新目录中核对[发布哈希](09-delivery-and-offline.md)。不要将生产录像、现场配置或模型放进仓库目录。
2. 安装 Python 3.12（桌面项目支持 Python 3.12–3.13）。在线准备电脑在仓库根目录打开 PowerShell，创建运行手册默认使用的隔离环境并安装核心、桌面与演示所需视觉依赖：

   ```powershell
   py -3.12 -m venv implementation\.venv
   .\implementation\.venv\Scripts\python.exe -m pip install -e '.\implementation[desktop,vision,windows]'
   .\implementation\.venv\Scripts\python.exe -m pip install -e .\desktop
   .\implementation\.venv\Scripts\python.exe scripts\verify_repository.py
   ```

   Mac/Linux 的命令对应为：

   ```sh
   python3.12 -m venv implementation/.venv
   implementation/.venv/bin/python -m pip install -e 'implementation[desktop,vision]'
   implementation/.venv/bin/python -m pip install -e ./desktop
   implementation/.venv/bin/python scripts/verify_repository.py
   ```

   依赖安装需要可访问 Python 包源；命令只创建项目本地环境并安装源码依赖，不下载模型、安装系统级驱动或启动采集。验证器失败时保存完整输出并停止；不要继续演示或修改冻结源码。离线部署需使用经校验的离线资源与目标平台专属运行环境，见[离线交付说明](09-delivery-and-offline.md)。

3. 运行只读设备探测，保存终端输出到本机演示证据目录：

   ```powershell
   .\scripts\start-desktop.ps1 -Probe
   ```

   ```sh
   ./scripts/start-desktop.command --probe
   ```

   结果包括 OS/架构/内存/NVIDIA 显存观察和建议；`capacity_status` 应为 `NOT_BENCHMARKED`，不是基准测试。探测失败时保留 null/unknown，不以设备管理器截图补写探测值。

### 运行并录制应用界面渲染

演示脚本使用真实 Qt 窗口和合成 Runtime。`--output` 目录必须事先不存在，时长必须在 125–180 秒之间；脚本会创建配置、事件数据库、会话 JSONL、进程信息、窗口截图和报告。`--render-recording` 额外采集 Qt 对实际主界面及所属子对话框的 paint 结果，生成 `application-demo.mp4` 和 `application-demo.json`；每帧带显著水印“实际应用界面渲染录制 / 合成输入 / 自动测试操作 / 非系统读屏 / 非现场验收”。脚本在预定时刻注入确认、稍后处理、误报和超时情境，并尝试播放本地产生的证据；按钮操作属于自动测试，不是人工现场操作。

准备一个全新的输出目录。PowerShell 示例：

```powershell
$run = Join-Path (Get-Location) 'runs\desktop-demo-001'
if (Test-Path -LiteralPath $run) { throw "输出目录已存在，请换唯一 run 名：$run" }
```

Mac/Linux 示例：

```sh
run="$(pwd)/runs/desktop-demo-001"
test ! -e "$run" || { echo "输出目录已存在，请换唯一 run 名：$run"; exit 2; }
```

在仓库根目录运行以下命令。它记录 Qt 界面绘制，不抓取操作系统像素、不请求屏幕录制权限、也不录制其它应用窗口：

```powershell
.\implementation\.venv\Scripts\python.exe desktop\scripts\demo_session.py --output $run --seconds 125 --render-recording
```

```sh
implementation/.venv/bin/python desktop/scripts/demo_session.py --output "$run" --seconds 125 --render-recording
```

Qt 渲染录制的 JSON 记录帧数、帧率、视频时长、墙钟时长、输入边界和 `field_gate=NOT_TESTED`。演示后确认 MP4 与 JSON 均存在，`frames_written` 大于零，水印清晰可读；再用本地播放器打开 MP4，核对界面过程、合成/自动操作/无模型标识和播放时长。此产物只显示程序绘制的 UI 状态，不是系统屏幕、Seetong 或视觉模型录制。若编码器打不开或中途失败，保留错误和不完整文件，换新目录重跑，不覆盖失败材料。

### macOS 原生录屏 helper 的诊断状态

仓库仍保留 `desktop/scripts/RecordApplication.swift`，用于研究 Apple [ScreenCaptureKit](https://developer.apple.com/documentation/screencapturekit) 的单窗口过滤器与流状态。它需要 macOS 15+、Xcode Command Line Tools 和屏幕录制权限，但目前不是已验证的录屏交付路径：主应用过滤、独立窗口过滤和 run-loop 对照均复现过约 4 个样本帧、0.33 秒的短片，无法满足长演示。helper 同时核对 AVAsset 时长至少达到目标时长的 90%，以及 AVAssetReader 读取的视频样本数不少于“目标秒数 × 2”；任一条件不满足就以退出码 1 返回并保留失败诊断。实测一个容器时长为 15.455 秒、却只有 4 个视频样本的文件会被拒绝。这个双重检查只验证动态应用演示文件的基本完整性，不适合用来判断静态幻灯片或静态画面，也不代表 ScreenCaptureKit 取流问题已修复。不要把进程启动、样本回调或 MP4 文件出现说成录屏验收成功。

如需重现诊断，先编译到仓库外的新路径（注意 `-parse-as-library`）：

```sh
xcrun swiftc -parse-as-library -O -framework AppKit -framework AVFoundation -framework ScreenCaptureKit desktop/scripts/RecordApplication.swift -o /tmp/factory-monitor-record-app
```

若要复核一个已有的动态演示文件，可先运行双重校验；当前已知失败样本会被拒绝：

```sh
/tmp/factory-monitor-record-app --verify-only /path/to/dynamic-demo.mp4 15
```

若要重新运行捕获实验，只对新的隔离目录操作，并预期该诊断工具可能按设计返回失败。应用创建 `process.json` 后，可用其中的 PID 和精确窗口标题调用：

```sh
pid=$(implementation/.venv/bin/python -c 'import json; print(json.load(open("runs/desktop-demo-001/process.json")) ["pid"])')
title=$(implementation/.venv/bin/python -c 'import json; print(json.load(open("runs/desktop-demo-001/process.json")) ["title"])')
out="$(pwd)/runs/desktop-demo-001/desktop-window.mp4"
test ! -e "$out" || { echo "录屏文件已存在，换新目录"; exit 2; }
/tmp/factory-monitor-record-app "$pid" "$title" 125 "$out"
```

`process.json` 路径必须与本次目录一致；输出仅在时长、视频样本数两项校验均通过且退出码为 0 时才算实验产物完成。即使通过这些检查，也不等于客户端界面或监控采集验收；此前重复诊断的短片应继续作为失败证据保留。Windows 现场仍需获准的原生窗口录屏工具完成 15–30 分钟连续演示，并读回视频与应用日志；这项现场 Gate 尚未完成。Microsoft 的[Windows Graphics Capture](https://learn.microsoft.com/en-us/uwp/audio-video-camera/screen-capture)是系统 API 背景资料，不能替代本项目在目标 Seetong 上的实际采集证据。

脚本创建 `demo-report.json`、`session.jsonl`、`process.json`、`desktop-final.png`、`config.json` 和 `data/`；启用 `--render-recording` 时还会创建 `application-demo.mp4` 和 `application-demo.json`。应用会在最后做持久化重启读回并退出。若窗口、事件或编码失败，保留终端输出与完整目录；检查依赖、Qt 平台插件和编码器后在新目录重试，不覆盖或删除失败材料。

核对 `demo-report.json` 中 `restart_preserved`、`recording_complete`、事件/待办和 `field_gate=NOT_TESTED`，检查 JSONL 的事件与自动操作时序，并播放 `application-demo.mp4` 回读水印、渲染内容和时长。该录制不读取系统屏幕，不能证明其他窗口、系统录屏权限、现场采集区域或监控软件画面。

演示没有真实模型请求，因此不能展示为“模型判断通过”；其中 timeout 是合成故障注入。若另行演示真实本地模型，必须采用本机已验证视觉模型，提供本地 loopback routes JSON，启动时显式加 `--routes <file> --enable-review`，并单独记录实际模型 tag/digest、输入类型和请求状态。routes 的字段契约可看[模型路由测试](../desktop/tests/test_models.py)；每个实际模型与负例语义都须独立验收，不能混剪为合成场景效果。

CLI 默认资源档位是 `vram8gb`，这不是容量上限。该档位做模型复核时必须用[固定 8GB 单路由模板](../templates/8gb-model-routes.example.json)，并先完成[受控预热和资源检查](13-8gb-and-capture-isolation.md)。通用 Ollama/OpenAI 路由、Mac 上没有 NVIDIA 的本地模型对照，都要显式传 `--resource-profile existing` 或 PowerShell `-ResourceProfile existing`；此模式没有 8GB 保护，亦不表示该设备已适配。Mac 的合成/视频演示可继续使用默认档位，但启用模型复核会因缺少 NVIDIA 资源证据而拒绝。8GB 以上设备也沿用保守基线，需按 docs/11 的升级 Gate 逐项提高输入或替换模型。

Windows 本地通用模型对照示例：

```powershell
.\scripts\start-desktop.ps1 -ResourceProfile existing -ConfigPath .\runs\model-trial\config.json -DataDir .\runs\model-trial\data -Source demo -Routes D:\approved-local\routes.json -EnableReview
```

Mac 上的本地模型对照示例：

```sh
./scripts/start-desktop.command --resource-profile existing --config "$(pwd)/runs/model-trial/config.json" --data-dir "$(pwd)/runs/model-trial/data" --source demo --routes /approved-local/routes.json --enable-review
```

## 目标 Windows：先准备再进入现场

Windows 是优先验证平台，但当前没有可用的目标机远程访问。本章命令只在操作员的目标机器或获准准备机执行。源码 ZIP 本身不含完整安装器、模型权重和 wheelhouse，不能当作离线一键运行包。已有 6.6 GB Windows 候选资源包按[Windows 执行手册](02-windows-execution.md)的六个入口和版本规则操作；不可将它与本仓冻结源码混为一个版本。

**在线开发/演示路径**：按上文在干净目录安装依赖。这是源码开发/演示环境，不是离线资源包内已锁定的 CUDA 运行环境，也不证明 PyTorch 使用 NVIDIA GPU。现场 live 前还需按目标机批准的驱动/运行库方案核验 GPU 可用性，并按照[冻结交接](../implementation/docs/WINDOWS_HANDOFF_zh.md)取得和核验 YOLO 权重。实时检测必需 `yolo11n.pt`；视觉依赖安装不会下载该权重。新建配置位于源代码 checkout 时，桌面入口默认指向仓库内 `implementation/models/yolo11n.pt`；标准 wheel 安装则默认指向配置目录旁 `models/yolo11n.pt`。新电脑在线安装不等于完整现场部署；Windows 客户端、捕获权限、驱动和 Seetong 窗口仍需人工准备。安装失败时保留包源、Python、平台和错误输出，不从另一操作系统复制 wheel 或 CUDA 环境。

**已准备离线候选路径**：保留它自身的源码、环境、Ollama、YOLO、CUDA wheels 与本机状态；按包内版本号和清单逐项核对。该路径使用包内原入口，不得从本仓运行缺少 `resources/`、`changes.patch`、wheelhouse 或 requirements 锁文件的旧 Prepare 脚本，也不得把旧包的成功报告视作本仓桌面运行证据。

### 在既有 6.6 GB Windows 环境上升级本仓 v0.2 wheel

这只升级已通过旧包 `1-prepare` 准备好的 Windows 候选环境，不是新电脑离线安装器。先按[发布说明](09-delivery-and-offline.md)下载并校验同一 v0.2 Release 附带的 `factory_monitor-0.1.0-py3-none-any.whl`、`factory_monitor_desktop-0.2.0-py3-none-any.whl` 与 `SHA256SUMS`，再核对旧 6.6 GB 包自己的版本、依赖和模型资源。把两个新 wheel 放在候选目录的 `release-v0.2` 子目录，并从该候选目录运行其已准备的 `.venv` Python。必须同时安装两个 wheel：旧候选包中的 `factory_monitor` 虽也显示 0.1.0，但其提交不同；只升级桌面 wheel 会继续沿用旧核心。

```powershell
.\.venv\Scripts\python.exe -m pip install --no-index --no-deps --force-reinstall .\release-v0.2\factory_monitor-0.1.0-py3-none-any.whl .\release-v0.2\factory_monitor_desktop-0.2.0-py3-none-any.whl
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m factory_monitor_desktop --probe
```

`pip check` 失败、wheel 哈希不符、Python/依赖来源不明或 YOLO 权重校验不通过时停止并保留输出；不要卸载旧环境或覆盖唯一原始包。成功后新建单独配置和数据目录，运行 `-Probe` 与合成演示确认升级可启动，再按[现场 Gate](05-validation-and-acceptance.md)单路验证。大模型、wheelhouse 和安装器不包含在源码 ZIP 中；不要把这里解释成源码 ZIP 可独立离线安装。

在新源码目录使用启动脚本时，通过 `-PythonPath` 指向刚升级环境的完整 Python 路径；脚本默认的 `implementation\.venv` 不会自动查找另一目录内的旧候选环境。回退时保留新版本失败报告，利用未改动的旧完整包在新的目录重新准备旧环境，不混用两个版本的核心和桌面 wheel。

首次运行桌面 GUI 时用专属配置和数据目录，命令从仓库根目录执行。若 `--config` 不存在，桌面会写入默认未校准配置，并关闭扩展模型复核；`--source demo` 只选定合成输入，真正采集要由人按界面“开始”。确认配置路径、输出盘剩余空间和目标进程后再运行：

受保护桌面当前会无条件阻止 `live + display`，即使整屏源已标为校准也拒绝开始，因为主工作台和证据窗口本身可能进入采集输入。要做受保护的 live 检查须配置受支持且已校准的目标窗口捕获；冻结核心仍有 display 诊断路径，但不得绕过桌面入口把它当作受保护现场模式。

```powershell
.\scripts\start-desktop.ps1 -ConfigPath .\runs\target-win\config.json -DataDir .\runs\target-win\data -Source demo
```

```sh
./scripts/start-desktop.command --config "$(pwd)/runs/target-mac/config.json" --data-dir "$(pwd)/runs/target-mac/data" --source demo
```

每次新的验收运行须使用新的配置/数据目录；不要并发打开同一个事件数据库。退出 GUI 会停止刷新器和证据播放，但开始后仍须先按“停止”并确认状态，再退出。如果配置文件已有人工校准，命令会加载而不是重置；确认其来源、哈希和版本后才可运行。已存在配置中的相对 `detection.model_path` 现在按配置文件目录解析；从旧运行目录迁移的配置如果因此找不到权重，可在启动时传显式 `-DetectorWeights`/`--detector-weights` 覆盖。该选项只覆盖 runtime 的深拷贝配置，不回写已有配置。相对显式路径按启动命令的当前目录解析。

```powershell
.\scripts\start-desktop.ps1 -ConfigPath .\runs\target-win\config.json -DataDir .\runs\target-win\data -Source demo -DetectorWeights .\implementation\models\yolo11n.pt
```

```sh
./scripts/start-desktop.command --config "$(pwd)/runs/target-mac/config.json" --data-dir "$(pwd)/runs/target-mac/data" --source demo --detector-weights "$(pwd)/implementation/models/yolo11n.pt"
```

## Windows 实机阶段门禁

先固定 Seetong 版本、显示分辨率、DPI 缩放、窗口标题/窗口句柄、客户端布局、10 路频道身份表、网格与详情 ROI、YOLO 权重哈希、检测帧率、复核模型和本地数据目录。窗口、比例、分辨率或布局变化需重新校准；截图坐标或旧 profile 不能代替当前窗口身份与当前画面读回。客户端仍由操作者手动切换；程序没有生产级点击或自动学习能力。

| 阶段 | 人工操作 | 保存与放行条件 | 任一失败的动作 |
| --- | --- | --- | --- |
| 0：准备 | 校验源码/包清单、Python 与依赖、YOLO 文件哈希；核对 Seetong 登录/授权、屏幕捕获权限、磁盘空间和显示缩放 | 新 run_id、软硬件/模型/配置指纹、启动输出、基准模板，捕获尚未开始 | 停止并保留报错；不降低哈希或权限门槛 |
| 1：单路 | 手工指定一个频道，确认窗口、相机 ID、时间和新帧连续；短时被动采集，检查每个事件与视频证据 | 一路零错绑、可读回的源帧/事件/录像、明确停止和重启结果；人工确认与误报分开记 | 立即停止该 run，未知时段保持 unknown，检查标定和采集源 |
| 2：三路 | 只启用已验证三路，另建独立配置副本和数据目录，按实际启用相机集合生成身份映射；不可只从十路配置禁用其余九路但留下映射条目 | 三路逐路新鲜度、身份、盲区、媒体可解码、候选和队列完整；无错绑 | 停止，不扩大；保持失败原始副本及配置指纹 |
| 3：十路 | 经批准后加载单独的完整十路配置，人工按序切换验证各频道和返回总览；重复至少 100 次切换并覆盖弹窗、失焦、遮挡和客户端布局变化 | 10/10 通道身份零错绑；每路心跳/盲区可追溯；完整 15–30 分钟演示，原视频及逐项证据可读回 | 任一错绑/盲区缺记即停止，回到单路或三路根因排查，不用重试次数掩盖失败 |
| 4：准确性与长稳 | 固定正例、困难负例和真实现场标注；验证模型/提示词版本；运行一个完整班次并计划 72 小时稳定性测试 | 指标阈值、时延口径、分母、误报、漏报、超时、未知、队列与恢复由独立验收者审阅；按平台分别签署 | 任一未达标就限缩能力、保持人工复核或回退；不能宣称无人值守 |

1→3→10 每阶段必须有独立、可回退的配置副本和新数据目录，防止 disabled 通道旧 `grid_identities` 造成配置契约校验失败，也防止覆盖已校准证据。入口、阶段实测和验收表见[客户端校准手册](04-client-calibration.md)与[验证协议](05-validation-and-acceptance.md)。100 次切换、15–30 分钟和 72 小时均是待执行 Gate，不是本地合成演示的结论。

## 证据、停止和回退

每个 run 记录开始/停止时间、目标平台与显示配置、仓库提交/工作树、当前客户端版本、配置/YOLO/VLM 哈希、每路启用集合、捕获后端、模型状态、持续时间、错误/未知/丢帧、逐路心跳、录像可解码结果、人工标签和操作员回退动作。模板与最小字段参考[现场运行模板](../templates/field-run.template.json)和[设备基准模板](../templates/device-benchmark.template.json)。生产画面、现场账户和未脱敏日志只留在获准的本机受控目录，不传入仓库或演示视频。

遇到捕获中断、通道身份不确定、画面被遮挡、磁盘少于 1 GiB、模型超时/错误、证据不完整或无法确认停止时，停止本级运行并将受影响时段标未知。桌面程序不自动删除录像；按操作流程先停止、等待文件关闭、验证 SQLite/WAL 与媒体不再写入，再人工归档到获批目录。空间恢复后新建 run_id；不能清空目录或覆盖失败证据来重新开始。实机证据仍未通过前，桌面演示仅证明界面与合成数据工作。
