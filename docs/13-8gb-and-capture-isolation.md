# 8GB 显卡预算与采集权限隔离

本文把“先从 8GB 单卡基线起步、降低对监控客户端权限的依赖”拆成两条待验证路线：把常规检测压到 CPU，让 GPU 只运行一个小型本地视觉复核模型；用只读画面采集替代客户端内存读取、界面注入和自动点击。8GB 是保守起点，不是系统上限；所有设备默认仍采用这一基线，硬件探测或更大显存不会自动提高帧率、画质、并发或路数。升级到 Apple Silicon、12–16 GB、24 GB 或第二台主机，都必须先通过同一采集、告警、资源、准确率和长稳 Gate，再逐项试验更高输入清晰度、频率或模型。当前没有可用的目标 Windows 现场远程访问；真实客户端、8GB 显卡、十路负载和模型效果均为 `NOT_TESTED`。

## 当前代码边界

冻结核心 [Windows 采集适配器](../implementation/src/factory_monitor/capture/windows.py)通过 `windows-capture` 提供程序读取指定标题的窗口，或读取指定显示器；帧进来后转换为 BGR，并进入长度为 3 的新帧队列，满时丢弃最旧帧。它不读取客户端内存，也不在此适配器内发送点击或键盘输入。桌面程序的 [启动保护](../desktop/src/factory_monitor_desktop/app.py)会拒绝整个 `live + display` 配置，因为桌面主界面与证据窗口可能落入被采集的显示器。不能把核心仍有 `display` 分支误写为受保护桌面已支持整屏 live。

当前桌面可用的探测入口只做只读硬件信息和建议；核心 `preflight` 也声明不采集屏幕、不请求权限、不更改 OS 设置。它们不能证明目标客户端帧可见、采集不被遮挡或实机容量。客户端 UIA 示教与自动 OS 点击仍未接入桌面，任何现场运行都由操作员手工操作客户端。

独立显示器采集是下一步架构方案：让客户端画面输出到专用显示器/视频输出，采集与告警工作台运行在另一个屏幕或另一台机器上；采集端只收像素帧，工作台不叠加在被观察输入里。这是降低客户端 UIA/控制权限耦合的架构推断，不是 Windows 对所有客户端、显示器或会话保证免权限。Windows Graphics Capture（WGC）既可选择应用窗口，也可选择显示器；官方 API 用系统选择器让用户选来源，并显示系统采集边框。[Microsoft 的屏幕采集说明](https://learn.microsoft.com/en-us/windows/apps/develop/media-authoring-processing/screen-capture)描述了窗口/显示器来源、系统选择器和采集边框。核心当前代码通过第三方 Python provider 按标题/显示器编号配置来源，不等同于本文建议的系统选择器体验；独立采集进程、输入隔离、黑帧/新鲜度门禁和桌面集成均未完成。

macOS 的 ScreenCaptureKit 能选择显示器、运行中的应用或窗口，并要求用户授予 Screen Recording 权限；它不能被描述为免权限采集。[Apple 的 macOS 示例](https://developer.apple.com/documentation/screencapturekit/capturing-screen-content-in-macos)还要求 macOS 15+ 与 Xcode 16+。本仓的 Mac 原生 helper 只是失败诊断工具，Windows 现场优先，Mac 需按 [macOS 执行手册](03-macos-execution.md)准备权限和依赖。

## 能力与权限耦合对照

| 路径 | 读什么 / 控制什么 | 对客户端权限的依赖 | 能解决什么 | 当前状态与限制 |
| --- | --- | --- | --- | --- |
| WGC 精确窗口捕获 | 操作系统交付的指定窗口像素；不操作客户端 | 通常可在普通用户会话内试验；不等于绕过受保护内容、登录桌面或客户端自身限制 | 隔离其他窗口，降低 UIA/点击和客户端内部接口依赖 | 冻结核心有适配器，桌面 live 现场仍未验证；窗口标题、窗口重建、权限与帧新鲜度都须实测 |
| WGC 显示器捕获 | 指定显示器的合成像素；不操作客户端 | 依赖系统允许的显示器采集能力与用户选择/授权 | 客户端无需开放进程内接口；可观察其可见输出 | 核心分支存在，但受保护桌面当前拒绝 `live + display`；只有完成独立输入输出隔离后才可评审接入 |
| 外接视频采集通道 | 客户端所在显示输出的像素，经获批采集卡/另一台采集主机读取；不操作客户端 | 不需要客户端 UIA/进程权限，但采集设备驱动、视频输出和物理布线仍需批准 | 将监控客户端与告警工作台拆开；客户端不必为采集代理提权或开放内部 API | 未集成。必须用专用输出和独立工作台防止反馈；受保护/加密输出、无信号、分辨率变化可能表现为黑帧/错误画面，不得绕过 HDCP 或设备策略 |
| 客户端 UI Automation（UIA） | 控件树、可访问性属性，并可能触发控件动作 | 进程完整性等级会影响访问；同为普通用户通常更直接，普通进程无法可靠驱动提升权限窗口。跨等级 UIAccess 需要签名与受保护安装位置，并带来安全风险 | 可读控件状态或在授权范围内进行辅助操作 | 本桌面没有 UIA 控制。Microsoft 说明 UIPI 会限制低权限进程与高权限进程交互；不要为了自动化启用 UIAccess、改 UAC/组策略或让程序自动提权。[Microsoft UIPI/UIAccess 安全说明](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-10/security/threat-protection/security-policy-settings/user-account-control-allow-uiaccess-applications-to-prompt-for-elevation-without-using-the-secure-desktop) |
| 客户端内存读取、消息注入、模拟点击 | 客户端内部状态或输入通道 | 权限耦合、维护和误操作风险最高 | 不纳入本方案 | 禁止将其作为绕过客户端权限的方案；桌面当前不执行自动 OS 点击 |

捕获画面与控制界面是不同权限面。单纯采集可见输出可以避免向监控客户端发送 UI 消息，但不代表系统会在锁屏、安全桌面、受保护窗口或远程桌面异常期间持续提供可信帧。Microsoft 的 UIPI 故障说明把锁屏、UAC、安全桌面、目标程序提升权限、屏保和最小化 RDP 列为 UI 自动化常见失败条件；这证明 UI 自动化对交互会话敏感，不足以推断 WGC 在这些场景的具体帧行为。[Microsoft 故障说明](https://learn.microsoft.com/en-us/troubleshoot/power-platform/power-automate/desktop-flows/ui-automation/uipi-issues)。本项目将这些条件作为必测暂停场景，而不依赖降低安全策略来保持运行。

## 8GB 候选预算：假设，先测再留用

当前桌面 CLI 默认 `--resource-profile vram8gb`，PowerShell 启动器也默认 `-ResourceProfile vram8gb`。这只是默认保守基线，不会因检测到更大显存自动扩负载。启用模型复核时还必须给出本仓[固定单模型 8GB 路由模板](../templates/8gb-model-routes.example.json)；该模板仅指向 loopback Ollama 固定模型，经本地资源保护网关运行。通用 Ollama/OpenAI 双协议实验路由必须显式选择 `existing`，这条路径不提供 8GB 资源保障，也不代表高配机器已适配。无 NVIDIA 的 Mac 可以运行不启用模型复核的合成演示；Mac 本地模型对照需使用 `existing` 并单独验收，不能把 8GB GPU 门禁视为已通过。

| 资源项目 | 8GB 单卡试验起点 | 约束和失败策略 |
| --- | --- | --- |
| YOLO 检测与证据采集 | 单张 NVIDIA 卡场景下强制 CPU YOLO；采集、检测、证据采样先按 2 fps | 这是待现场验收的资源配置，不是每路吞吐保证。多路时需按总裁剪数和队列龄实测。2 fps 会减少单位时间证据帧数，但不得改动前置/后置时长。 |
| GPU 视觉复核 | 只允许 Ollama 中一个预热的固定模型 `qwen3-vl:2b-instruct`，digest 固定为 `ea422f1e73652a95479954d8572d3c8c6022f628ce2d38a1a04aae1b7f2d5300`，量化 `Q4_K_M`，单在途请求，模型显存预算不超过 4 GiB | Ollama 官方条目列出该 tag、Q4_K_M、约 1.9 GB 权重和 Ollama 0.12.7 起的版本要求；权重文件大小不是运行峰值显存。常驻模型保持 `keep_alive=-1`，不让空闲卸载变成下一次冷加载；操作者退出 Ollama 或显式卸载后，须重新执行受控预热才能启用复核。[Ollama 固定模型条目](https://ollama.com/library/qwen3-vl:2b-instruct)、[Ollama keep-alive 说明](https://github.com/ollama/ollama/blob/main/docs/faq.mdx)。 |
| 上下文与输出 | `num_ctx=4096`、`num_predict=256` 作为硬上限 | Ollama 支持通过运行参数限制上下文和最大生成 token 数。[Ollama 参数说明](https://docs.ollama.com/modelfile)。启动后仍要记录实际 model digest、上下文/输出配置、prompt token、超时和端到端时延；不得只凭配置字段断言峰值被控住。 |
| 视觉帧与像素 | 每次最多保留 6 帧，严格按时间顺序和原始 timestamp 输入；逐帧最长边先限到 448 像素 | 这是预算约束，不是准确率承诺。Qwen 官方视觉处理可设置图像尺寸/像素预算；缩小后丢失的字、工件细节和边界不能靠插值恢复。[Qwen3-VL 官方像素预算说明](https://github.com/QwenLM/Qwen3-VL#pixel-control-via-official-processor)。保留完整原始证据供人工查看；小字/身份/细部不清时必须 unknown。 |
| 显存与系统余量 | GPU used ≤5 GiB 且 free ≥3 GiB；系统 `RAM available` ≥4 GiB | 这是新资源准入门槛：至少约 2 GiB 留给 OS/客户端/桌面，再留约 1 GiB 未测峰值余量。只支持一张明确识别的 NVIDIA 卡；多 GPU、GPU 信息缺失、报告过期或其他厂商拒绝复核。显存观察每 2 秒采样，缓存 10 秒失效；历史 used 曾超过 6 GiB 后熔断至进程重启。 |
| 证据录像 | 采集/证据采样 2 fps，保持核心 30 秒前置 + 60 秒后置时长 | 不因 8GB 预算缩短时长。2 fps 只改变帧密度；录像完整性、磁盘增长和多路写入仍需现场测量。空间不足按桌面低空间保护停止采集并人工归档。 |

当显存准入失败、监测信息不可信或熔断已触发时，只拒绝/跳过 VLM 复核，候选应继续标记 unknown 并留给人工；不能把拒绝解释为显存已释放、模型已卸载或后台推理已终止。不要在运行期间自动加载冷模型，避免峰值未知；模型常驻只是减少冷启动，不构成峰值限制。

448 像素长边是偏激进的图像预算。它可能适合大物体、区域变化等粗筛，但不保证读清时间戳、相机号、工件刻字或细小目标。Qwen3-VL 官方仓库允许配置图像像素预算；“调低像素会丢失细节且插值不能恢复原始信息”是基于缩小采样过程的工程推断，不是该模型官方给出的准确率结论。验证集必须含原始分辨率参考、缩放后图、困难负例和小目标例；若缩放造成关键线索不可辨，降低模型角色到“提示人工复核”，或把该类任务送回原始分辨率的人工证据查看，不能放宽通过阈值。

CPU 跑 YOLO、采集与证据都按 2 fps 可能增加 CPU 饱和和候选等待；Q4 权重、4096 上下文、256 输出和 448 长边也不能单独推出整机显存或准确率。每次只改一个预算项，记录软件提交、权重哈希、模型 digest、图片尺寸、总帧数、CPU/GPU/RAM/VRAM 峰值、端到端时间、队列龄、超时/unknown、漏帧、误报和证据文件。任一资源越线或状态数据超过 10 秒就拒绝复核并保存 unknown；更强硬件只在实测瓶颈后讨论，不承诺硬件 FPS、价格或十路容量。

## 普通用户会话部署与只读预检

目标验证从 Windows 普通用户交互会话开始。监控进程、桌面程序和 Ollama 先都以普通用户运行；监控代理不要求客户端开放私有 API、进程内存、管理员密码或 UIAccess。如果客户端本身必须由 IT 以管理员运行，先在不控制客户端的前提下测捕获可见性；拿不到可信帧就停止，不用同意提权、修改 UAC、放宽 secure desktop、关闭屏保/安全策略来“修复”。Microsoft 指出 UIAccess 可跨 UIPI 完成交互，但要求可验证的签名与受保护安装位置，并能影响 UAC 安全桌面，安全影响需单独评估；这不是本项目建议依赖的普通权限路径。

从新仓库根目录执行现有只读命令。PowerShell 硬件探测：

```powershell
.\scripts\start-desktop.ps1 -Probe
```

它只输出硬件观察和建议，不测试 YOLO/VLM、WGC、客户端画面或实际 FPS。已准备好配置后，核心只读 preflight：

```powershell
.\implementation\.venv\Scripts\python.exe -m factory_monitor preflight --config .\runs\win-pilot\config.json --data-dir .\runs\win-pilot\data
```

```sh
implementation/.venv/bin/python -m factory_monitor preflight --config runs/mac-pilot/config.json --data-dir runs/mac-pilot/data
```

Preflight 不打开捕获、不申请系统权限，也不修改 OS；它的 `field_gate` 会明确未通过现场条件，不能把 `ok=true` 解读为采集已就绪。仅在批准的短时现场试验中，由操作员确认准确客户端/窗口/分辨率与采集来源，然后才用已有桌面命令手动启动已校准的目标窗口 live；从仓库根目录的启动入口见 [Windows/现场步骤](12-demonstration-and-field-run.md)。不要为独立整屏输入把 `source=live` 与 `backend=display` 填入受保护桌面：当前它会明确阻止启动。未来独立屏幕采集须先实现单独输入显示器、独立告警工作台、拒绝自采、黑帧/停滞检测与一键停止，再经过代码审查和现场测试，才可重新评审该保护门。

当前源码已有资源采样、`Vram8gbPolicy`、默认 `vram8gb` CLI、桌面显存状态栏和启动入口。状态栏显示观察到的显存/拒绝状态，不是显存峰值保证或现场容量结论。运行时启动由 `scripts/start-ollama-8gb.ps1` 托管的 Ollama（本进程环境、loopback 11435、单模型/单并发、4096 context、禁云）；该脚本只启动服务，不预热模型。随后在新报告路径运行受控准备器：

先在独立 PowerShell 窗口启动服务并保持该窗口打开。下面两条绝对路径是占位示例，须替换为已校验的 Ollama 程序和离线包 `resources/models/ollama` 目录；端口占用会拒绝启动，不会终止原服务。

```powershell
.\scripts\start-ollama-8gb.ps1 -OllamaPath 'D:\approved\ollama\ollama.exe' -ModelsPath 'D:\approved\resources\models\ollama'
```

```powershell
New-Item -ItemType Directory -Force .\runs\8gb-preflight | Out-Null
if (Test-Path .\runs\8gb-preflight\warmup.json) { throw "报告已存在，换新的运行目录" }
.\implementation\.venv\Scripts\python.exe desktop\scripts\prepare_8gb_model.py --port 11435 --report .\runs\8gb-preflight\warmup.json
```

```sh
mkdir -p runs/8gb-preflight
test ! -e runs/8gb-preflight/warmup.json || { echo "报告已存在，换新的运行目录"; exit 2; }
implementation/.venv/bin/python desktop/scripts/prepare_8gb_model.py --port 11435 --report runs/8gb-preflight/warmup.json
```

准备器冷加载时要求已安装的固定模型、空闲显存至少 6 GiB、零个已加载模型；它校验 `/api/tags` digest、请求 `keep_alive=-1` 预热，再读取 `/api/ps` 和加载完成后的新显存采样。若唯一正确模型已经预热，则只验证当前准入条件，不再次加载，也不要求冷加载的 6 GiB 空闲。它不下载模型、不提权、不自动重试、不卸载或终止服务；每次须用新报告路径。非 NVIDIA、压力/传感器未知、加载状态不符或报告已存在时会拒绝/失败，保存报告与资源日志并人工检查，不要绕过门禁。桌面复核命令也必须传 `--routes templates/8gb-model-routes.example.json --enable-review`；开启复核但缺少 routes 时会 fail closed。不要直连 Ollama API 绕过网关。上述代码已集成，但 8GB 卡上的长期峰值、十路性能和模型准确率仍待实机验证。

完成受控预热后，从仓库根目录启动合成验证：

```powershell
.\scripts\start-desktop.ps1 -ConfigPath .\runs\8gb-demo\config.json -DataDir .\runs\8gb-demo\data -Source demo -Routes .\templates\8gb-model-routes.example.json -EnableReview
```

只有在获准现场且通过单路校准后，才把 `-Source demo` 改为 `-Source live` 并使用已验证窗口配置。若资源门禁拒绝，模型复核不可用，候选留 unknown 由人工复核；不得切换到通用路由或加大输入来绕过拒绝。

核心还提供 `implementation/scripts/benchmark_detector.py`（组件级 YOLO 延迟/RSS）和 `implementation/scripts/benchmark_local_review.py`（本地 VLM 请求/超时观察）。二者不是现场验收：前者源码默认路径绑定到旧本机的模型/测试图，运行前必须为 `--model`、`--image`、`--output` 指定当前授权文件；后者也需要明确的公开/获准测试图和本地 endpoint，且不会评估现场分类准确性。不要复制脚本默认绝对路径或用敏感现场图做试跑。其字段和边界以[实际脚本](../implementation/scripts/benchmark_detector.py)及[本地复核基准脚本](../implementation/scripts/benchmark_local_review.py)为准。

## 现场测试矩阵与暂停门禁

每项至少保留开始/结束时间、OS build、用户权限等级、客户端版本、窗口/显示器身份、分辨率、DPI、布局指纹、采集后端、每帧尺寸与单调时间、丢帧/重复帧/黑帧比例、十路身份回读、GPU/CPU/RAM/VRAM 峰值、证据录像完整性和操作员动作。用获准的测试账号和测试画面；不记录密码、令牌或员工个人资料。

| 场景 | 检查 | 放行条件 | 任一不满足 |
| --- | --- | --- | --- |
| 普通用户登录；客户端也为普通用户 | 选择精确客户端窗口；确认帧尺寸、标题/身份、画面与采集时间持续变化 | 连续新帧、窗口/频道映射正确、采集不含工作台，证据可回读 | 暂停；保留失败日志，标记受影响区间 unknown |
| 客户端以管理员身份运行、采集进程仍是普通用户 | 仅观察画面，不执行 UIA/点击；比较同一授权测试源在客户端普通/提升模式的可见帧 | 仍有稳定的正确帧，且无提权、策略变更 | WGC/窗口帧不可用或来源身份变动就 unknown；不自动提高监控进程权限 |
| UAC 提示、安全桌面、锁屏、屏保 | 在获准维护窗口观察捕获端状态；不得触发或自动应答生产 UAC | 这些状态有清晰暂停信号，恢复后需重新确认窗口/频道和新帧 | 不能确认时立即暂停，标整个中断区间 unknown；恢复到交互桌面后人工重验 |
| RDP 正常、断开、窗口最小化 | 分别比较活动会话、断开、远程窗口最小化下的帧时间戳/内容 | 每个状态要么提供新鲜且一致的有效帧，要么让系统检测到暂停 | 任何冻结/重复/缺帧不得沿用最后一帧继续判定；暂停并标 unknown |
| 客户端最小化、被遮挡、失焦、弹窗覆盖 | 观察窗口捕获与显示器捕获是否行为不同；特别检查证据播放器/主工作台是否进入输入 | 精确窗口仍提供真实新帧，或状态显式暂停；显示器方案没有自采反馈 | 黑帧、旧帧或自采均停止；停止录像完整化并标 unknown |
| 受保护窗口/加密内容/黑帧 | 使用无敏感测试画面验证采集源黑帧识别；不得尝试绕过保护 | 黑帧能可靠告警并暂停；有效输入恢复后人工批准恢复 | 将画面记为不可用/unknown；不把黑帧当无人、空设备或正常负例 |
| 分辨率、DPI、排列、显示器编号、客户端布局变化 | 逐项改变后检查窗口/显示器指纹、帧宽高、规范化 ROI 和各相机频道 ID | 变化导致配置失效提示；重新校准后映射零错绑 | 立即停止并退回上一个已验证配置副本，不把旧 crop 用在新尺寸 |
| WGC 重启、设备拔线、采集卡失联或系统睡眠恢复 | 验证 closed/device lost/no-frame 错误、停止时长、未完成事件标记 | 错误会转为暂停/unknown，保存不完整证据，且没有静默自动恢复 | 保留失败 run；新 run_id 重新 preflight 和逐路核对 |

“暂停并标 unknown”是采集接入必须达到的要求，不表示当前软件已对每种操作系统/驱动黑帧提供完备传感器。恢复时不能用补帧、插值、重复最后一帧或缩短 30+60 秒证据窗口伪造连续性。正常用户会话测试先过，再单独由管理员配合测试客户端提升场景；任何权限不足都以功能降级和人工复核处理，不改变电脑安全策略。

## 放行标准

1. 在批准的目标 Windows 上，普通用户进程可稳定采集正确目标窗口/独立输入；桌面工作台和证据播放不会进入观察画面。
2. 对锁屏、UAC、安全桌面、远程会话断开/最小化、窗口最小化、遮挡、黑帧、分辨率/DPI/布局变化和采集设备丢失，每项都能在限定时间内停止自动判断并把影响区间标为 unknown。
3. 以单 NVIDIA 卡、CPU YOLO/采集/证据 2 fps、一个固定 digest Qwen3-VL 2B Q4、6 帧/4096 context/256 max output/最长边 448 的候选配置跑完整代表负载；准入要求全卡 used ≤5 GiB、free ≥3 GiB 且系统 RAM available ≥4 GiB，并验证 10 秒显存数据过期、>6 GiB 熔断和重启恢复。超时或资源压力时模型复核被拒/跳过，人工待办与原始 30+60 秒证据仍保留。
4. 30 秒前置 + 60 秒后置视频在峰值负载下无窗口缩短、丢失、错路或不可播放；每事件可对账频道身份、输入时间、视频时间、处理结果、unknown 时段和人工结论。
5. 再进行目标 8GB 卡、10 路、至少 15–30 分钟实机演示与故障复位；生产准确率、轮班长稳和 72 小时连续运行另设独立验收。全部结果都需对照[现场验收协议](05-validation-and-acceptance.md)签字。

### 8GB 基线以上的逐级升级 Gate

8GB 基线不限制后续设备升级。Apple Silicon、NVIDIA 12–16 GB、NVIDIA 24 GB 或第二台推理机都先复制隔离配置与数据目录，维持当前 2 fps、30+60 秒证据窗口、单在途模型请求和 unknown 策略，使用相同的采集/身份/告警样本跑基线。每次只变更一个因素（例如输入清晰度、采集频率或模型），保留旧配置、权重和证据，并重跑设备资源、准确率留出集、采集异常暂停、15–30 分钟现场试跑与长稳验收。只在已测瓶颈对应的 Gate 通过后再留用该项升级；失败则停止、标记受影响区间 unknown，并恢复上一个已验证配置和设备路径。`existing` 是不带 8GB 保护的显式实验路径，不是高配自动适配方案。

以上条件尚未有现场证据，当前结论仍是 `NOT_TESTED`。不得把合成运行、只读设备探测、公开样本基准、单模型响应或“8GB 权重能加载”写成 8GB 十路可用、客户端权限无依赖或视觉准确率合格。
