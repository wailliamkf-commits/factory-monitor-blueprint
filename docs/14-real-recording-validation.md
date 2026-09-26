# 真实录像与本机读屏验证

从 Seetong 录屏验证时，使用真实文件，保留原件；禁止用合成画面替代。以下新增工具属于诊断扩展，不改变冻结的 `implementation/`，也不是已经完成的生产读屏服务。

## 分开验证三件事

1. **录像文件读取**：整段解码检查、按录像原始时间抽样、逐路切割、人物检测、摄像头时钟 OCR。结果只能说明这份录屏。
2. **Mac 播放器窗口读屏**：把真实录屏按原速显示在独立窗口；采集器只绑定这个窗口的 PID 和标题。播放、暂停、恢复时必须检查实际视频像素或摄像头时钟。系统回调次数和系统时间不能代替新鲜度证据。
3. **Windows Seetong 现场**：真实窗口、权限、最小化/遮挡/锁屏、布局切换、1→3→10 路和长时间负载另行验证。前两项通过不能代替这一项。

九宫格压缩到约 960 像素宽时，每路只有约 320 像素宽。此时可能看得到场景和部分人物，却读不出时钟，也看不清手部与小物件。读不清必须返回未知。增加 GPU、插值放大、超分辨率不能作为细节真实性证明。总览负责发现候选，清晰单路负责复核；需要覆盖盲区时使用并行总览或更高分辨率来源。

## Mac 本地诊断工具

前提：macOS 15 或更新版本、Python 3.12、已安装本仓库桌面依赖、OpenCV、Ultralytics、已下载的 YOLO 权重；Swift 命令行工具以及 macOS Vision。OCR 使用 [Apple Vision](https://developer.apple.com/documentation/vision/vnrecognizetextrequest) 的本地识别，仅用于本机验证，不冒充已部署的国产 OCR 或 Qwen 行为模型。入口在导入检测库前设置离线模式、关闭自动安装，再在独立设置目录中关闭遥测；只加载已存在且类别 0 为 person 的本地权重，不调用图像云服务。设置依据见 [Ultralytics 隐私说明](https://docs.ultralytics.com/help/privacy)。这不替代部署机器的独立网络审计。

从仓库根目录执行，以下命令会在忽略目录中创建校验文件和结果：

```sh
mkdir -p evidence/local/real-video
xcrun swiftc -O desktop/scripts/RecognizeClock.swift \
  -o evidence/local/real-video/clock-ocr -framework Vision -framework Foundation
python desktop/scripts/inspect_real_video.py \
  --input /absolute/path/to/recording.mp4 \
  --profile /absolute/path/to/private-profile.json \
  --output evidence/local/real-video/new-run \
  --ocr-helper evidence/local/real-video/clock-ocr \
  --weights /absolute/path/to/yolo11n.pt --step 2 --detect-every 30
```

`--output` 必须尚不存在，避免覆盖旧结果。每次按源时间戳定位；与原始 FPS 不同的抽样率不会把视频慢放。处理速度不能当作实时现场性能。旧冻结核心的 `video` 模式逐帧读取并按检测 FPS 等候，不能直接用它证明按原速回放；本工具独立使用源 PTS，不修改或掩盖这一限制。

私有校准文件示例（尺寸和区域必须按实际素材重新量取）：

```json
{
  "size": [1280, 720],
  "segments": [{
    "id": "single-layout",
    "start": 0,
    "end": 60,
    "cameras": [{
      "id": "view-1",
      "crop": [0, 0, 1280, 720],
      "clock": [0, 0, 420, 48]
    }]
  }]
}
```

`crop` 为原始解码像素中的 `[x,y,width,height]`；`clock` 相对这一频道裁剪。`start/end` 为录像秒数，右端不包含。没有有效区间的帧为 `layout_unknown`；尺寸变化、越界、重叠区间中止运行。**区间来自人工校准，不是自动学习到软件布局**；不能把某段录像的秒数规则用于真实桌面。素材中摄像头时间可能在左上角，不能硬编码“右上角”。

结果文件：`observations.jsonl` 保留逐样本 OCR、源 PTS、状态、对应抽样人物框；`report.json` 保存源与配置摘要、抽样统计和处理时间。`parseable_clock_samples` 是可解析数量，不是准确率。RSS 只采样当前进程树，不包括全部机器、显存和摄像头客户端，不能据此声称 8GB 验收。

时钟状态只使用完整日期与时分秒；不会用推算时间填补 OCR 失败。不可读、倒退、大跳、累计速度偏差、过大采样间隔立即变为 `unknown`。连续读到同一时间达到阈值才为 `suspected_stalled`。此机制仍依赖 OCR 真值和正确频道校准，不能因为有符合格式的文本就宣称实时画面正常。独立保留未用于调参的图像与人工读值，报告错误和未知样本。

## 查看真实素材和计算结果

本地清单例子：

```json
{
  "clips": [{
    "label": "真实录像 A",
    "video": "/absolute/path/to/recording.mp4",
    "profile": "/absolute/path/to/private-profile.json",
    "report": "/absolute/path/to/new-run/report.json",
    "observations": "/absolute/path/to/new-run/observations.jsonl"
  }]
}
```

```sh
python desktop/scripts/review_real_video.py \
  --manifest /absolute/path/to/private-manifest.json \
  --url-file evidence/local/real-video/review-url.txt
```

在本机打开输出文件中的 URL。只监听 `127.0.0.1`，随机路径，仅提供清单里的 MP4 视频；没有目录浏览。启动时复验素材与校准文件摘要，避免给错录像贴上检测结果。页面可以切换录像、暂停、拖动，并显示对应抽样结果。它是**离线结果回放**，不运行实时推理，暂停播放器不会发现场告警。检测框仅在其样本附近显示，未抽样的动作不能从这页判断；无检测框不能证明无人。浏览器按显示宽高比缩放时，叠加框仍以解码像素坐标同比显示。

`Ctrl+C` 停止本地查看服务；清单/素材路径失效后需重新启动。原始文件始终只读，不参与下载缓存清理。私有清单、现场视频、截图、OCR 原文、人员检测记录和本地模型适配数据都不得提交到公共 GitHub；只发布通用程序、测试和说明。

## 验证与后续接入

```sh
PYTHONPATH=desktop/src:implementation/src python -m pytest \
  desktop/tests/test_source_clock.py desktop/tests/test_real_video_tools.py -q
python scripts/verify_repository.py
```

Windows PowerShell 用 `$env:PYTHONPATH="desktop/src;implementation/src"` 后单独运行测试命令。Mac Vision OCR 不支持 Windows，工具会明确拒绝。Windows 后续需接本地国产 OCR 适配器，再用同一组独立真实样本验证；不是更换模型名称就算接通。

当前新增 `ClockTracker` 是诊断用模块，尚未接入桌面主程序的告警路由。正式接入时须把“读屏丢失/布局未知/时钟未知”作为系统健康提示，与越线等行为候选分开；真实连续读取、异常弹窗、恢复、重启、消息限流需要一起测试，不能以本次离线工具替代。

## Mac 独立窗口采集诊断

新增 `CaptureWindow.swift` 明确保持 `SCStream` 和输出对象的生命周期，只接受指定 PID 与完整标题唯一匹配的窗口，只交付完整图像；回调时间仍不是摄像头时间。不要从这项修复反推旧合成录屏故障的根因：本次确证的是新诊断程序中对象生命周期导致的零帧问题。

先打开自己选择的真实录像播放器，并取得其实际 PID、完整标题及窗口大小。以下诊断只读该窗口，最多运行 60 秒（默认 30 秒），不会操作播放器；需要系统已有的屏幕录制权限。不会退回整屏捕获。

```sh
python desktop/scripts/capture_window_probe.py \
  --pid 12345 --title '替换为已核对的完整播放器窗口标题' \
  --width 960 --height 544 --seconds 30 \
  --output evidence/local/real-video/new-window-run
```

这个命令会编译本地 Swift helper；缓冲队列限制为两帧，另记消费丢帧。没有图像持续五秒则失败；到期终止自己创建的 helper。保存少量窗口 PNG、固定中心区域的像素摘要和回调日志，不采音频。输出 `PIXELS_RECEIVED_ONLY` 只代表收到系统图像，不是现场通过。

要验证真实视频更新，请在此窗口做“播放 10 秒→暂停 10 秒→恢复 10 秒”对照，人工确认窗口中的摄像头时钟或明确可见运动。暂停段 ROI 应稳定，恢复后重新变化；时钟不可读仍记未知。仅数字、按钮或鼠标变化不能证明摄像头画面更新。该 CLI 与桌面主程序的旧捕获适配器独立，尚未把这一修复直接替换进冻结核心。
