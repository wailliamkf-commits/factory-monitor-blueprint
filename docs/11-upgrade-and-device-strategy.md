# 设备升级与模型适配策略

本章用于回答“哪台设备值得升级、升级哪一部分、换模型会不会改变检测结果”。结论必须来自同一输入与同一目标机器上的测量。桌面程序里的硬件探测只读取操作系统、架构、内存和可用 NVIDIA 显存，输出的 `capacity_status` 固定为 `NOT_BENCHMARKED`；它不会跑负载、测吞吐或自动安装驱动/模型。升级判断要把该探测和[设备基准记录](../templates/device-benchmark.template.json)分开填写。

## 当前实现与边界

- 桌面层是 Python 3.12、PySide6 程序，调用冻结的 `implementation/` 核心；新界面、弹窗、模型路由和设备建议放在 `desktop/`。不要直接编辑冻结核心来试验设备适配。
- 实时采集运行会构造 YOLO 检测器，需在目标系统上准备并校验 `yolo11n.pt`。新建源代码 checkout 配置时，默认绝对路径是 `implementation/models/yolo11n.pt`；标准 wheel 安装则默认在配置目录下 `models/yolo11n.pt`。它是实时检测前置条件。桌面模型路由是其后的可选视觉复核，不会替代检测权重。权重来源、SHA-256 与许可核对见[冻结来源的 Windows 交接](../implementation/docs/WINDOWS_HANDOFF_zh.md)；Mac 权限、平台依赖和窗口捕获步骤见[macOS 执行手册](03-macos-execution.md)。只复用经许可和哈希核验的模型文件；不要把 Windows 可执行文件、CUDA wheel 或环境复制到 Mac。
- `ModelRouter` 当前支持本机 loopback 上的 Ollama `/api/chat` 与 OpenAI-compatible `/v1/chat/completions`。它接受一个主路由和至多一个备用路由，总截止时间小于 15 秒、最大并发为 1，并校验结构化结果。路由配置可见，协议测试覆盖响应与失败边界；网关只有进程内 `last_trace`，尚未把真实提供方/模型尝试完整写入每条事件或持久审计日志。超时、错误、繁忙或无效结果必须保持未知/待人工处理。支持协议不意味着任意模型都适合该任务。
- 本地 Ollama 或其他兼容服务必须由操作者预先安装、加载视觉模型并单独验证。本桌面不会下载模型。新建配置默认关闭扩展复核；加 `--enable-review` 会为本次运行启用它。若提供已有配置且其中 `review.enabled=true`，未传该开关时仍沿用已有启用值。生产摄像画面默认不发送到公网；扩展路由只接受回环 HTTP 端点。
- 一个非模态弹窗承载持久化候选，提供查看证据、已阅、稍后 60 秒、人工确认和标记误报。关闭弹窗会稍后处理，不删除候选；事件仍留在 SQLite。受保护桌面拒绝启动整个 `live + display` 模式，而不只是抑制弹窗，因为主工作台和证据窗口都可能进入整屏采集；核心中的 display 入口仅保留诊断，不是受保护桌面可绕过的现场路径。经校准的精确窗口采集仍须现场验证采集区域、遮挡和窗口指纹。
- `ProtectedRuntime` 不自动清理完成的录像；数据目录低于 1 GiB 可用空间时不启动，运行时检测到低于该值会停止采集并要求人工归档。归档必须按现场保留策略进行，并先确认没有进程写入数据库或媒体。

核心接口与验证边界见[架构说明](01-architecture.md)、[路线图和容量限制](07-roadmap-and-capacity.md)、[现场验收协议](05-validation-and-acceptance.md)。本桌面代码仍没有对端到端 worker 队列的公平调度与完整队列账，也没有把“无人但物体/场景发生变化”的观察实验接入 runtime；客户端 UIA/一次人工示教状态机、模型端到端截止时延优化和跨平台长期稳定性均属待实现/待验收项。自动 OS 点击目前禁用。

## 十项优先事项与验收条件

| 优先级 | 事项 | 当前状态 | 进入目标现场前的验收 |
| --- | --- | --- | --- |
| 1 | 交付软件形态 | 已有 PySide6 桌面应用和仓库启动脚本；尚无已验收的 Windows 安装器/签名应用或 macOS `.app` 包 | 目标 OS 干净电脑从获批交付介质安装/启动/升级/卸载；哈希、依赖、权限、数据保留和上一版回退均读回通过 |
| 2 | 异常候选提醒 | 单个非模态弹窗、冷却提示和待办数量已集成 | 合成窗口录制检查无多窗抢焦；目标机现场覆盖候选、超时、未知和连续候选，所有候选 ID 与持久化队列一致 |
| 3 | 人工处置 | 已阅、人工确认、误报、稍后 60 秒均写入本地持久化 inbox；模型迟到不重开终态 | 重启后读回相同处置状态；SQLite 事件、收件箱和人工标注可逐 ID 对账 |
| 4 | 证据查看 | 本地事件证据用应用内 OpenCV 解码回放；路径限于当前数据目录且文件须存在 | 逐项检查视频实际解码帧和事件时刻；缺失/损坏/越界证据必须阻止播放并保留失败原因 |
| 5 | 采集不受界面反馈污染 | 受保护桌面目前拒绝启动 `live + display`，因为整屏输入会包含主工作台与证据窗；仅显示器录制不受保证。已标定、受支持的目标窗口 capture 才可能显示 live 弹窗 | 在每个后端检查同屏遮挡、弹窗、最小化、失焦和窗口变化；目标窗口的采集区域必须不包含主工作台/证据窗。不要通过冻结核心的 display 诊断入口绕过桌面保护 |
| 6 | 本地模型可替换性 | Ollama/OpenAI-compatible loopback router、一个备用、总截止 <15 秒、并发 1 已实现；不自动下载模型 | 对确切模型/tag/hash 做正负留出集、端到端 deadline、unknown/timeout、断连和重启测试；生产图像保持本地 |
| 7 | 设备识别和升级建议 | 只读平台/RAM/NVIDIA VRAM 探测；结果为 `NOT_BENCHMARKED` | 用设备基准记录实际完整负载；独立审阅者确认瓶颈、升级触发和回退，不根据探测建议直接采购 |
| 8 | 全链路公平队列与截止时间 | 未实现统一的采集→检测→候选→模型→证据公平调度/端到端队列账；模型槽虽有界为 1 | 固定压力输入对账 accepted/rejected/evicted/completed/timeout/shutdown；包括排队在内端到端 p95、遗漏和恢复均通过 |
| 9 | 无人物体变化监测 | 隔离实验逻辑尚未接 runtime，也不产生现场告警 | 接入前补足每路时间/身份/heartbeat/证据闭环，并在无人负例、物体变化正例和遮挡数据上独立评估 |
| 10 | 客户端示教与自动控制 | 仅人工操作；UIA/坐标示教、确定性动作状态机和 OS 点击均未接入/禁用 | 目标客户端版本/DPI/layout 绑定；动作有 request/frame/camera 回执并经至少 100 次现场切换零错绑，任何未知时安全冻结 |

状态只描述当前代码边界；“已有”不代表目标 Windows、准确率、十路或长稳验收通过。上述验收证据写入[现场运行和演示记录](12-demonstration-and-field-run.md)及[现场验证协议](05-validation-and-acceptance.md)。

## 先定位瓶颈，再决定升级

8GB 是保守运行基线，不是系统上限。CLI 与 PowerShell 启动器默认使用 `vram8gb`，即使设备探测建议更高档位，也不会自动增加输入频率、像素、并发或模型规模。默认 8GB 复核必须配合固定的[单模型 Ollama 路由](../templates/8gb-model-routes.example.json)和已通过的受控预热；Mac 无 NVIDIA 时可以跑不启用复核的合成演示，但本机 VLM 对照应显式选 `existing`。通用 Ollama/OpenAI 双协议路由也必须显式选 `existing`，该路径不受 8GB 策略保护，不能称作高配适配已完成。具体预热和门禁见[8GB 与采集隔离](13-8gb-and-capture-isolation.md)。

每次基准都固定仓库提交、配置哈希、YOLO 权重 SHA-256、视觉模型 tag/digest、输入片段及其哈希、分辨率、检测帧率、ROI、并发和运行时长。每个样本记录从源帧到人工可操作候选的端到端时间，而不是只记录一次模型调用。至少分别记录采集/解码、YOLO 检测、候选等待、模型等待和推理、录像编码/落盘、弹窗显示、人工确认时间，以及超时、丢帧、未知、证据缺失和磁盘变化。保留原始日志和逐事件结果；不要只报均值或删掉失败样本后再算 p95。

若采集帧已旧，换更强 VLM 不会修复采集；若检测卡在 YOLO，换 VLM 也不会加快检测；若主要耗时在录像或磁盘，扩显存没有证据支持。先用相同素材分别跑空闲、代表负载与峰值负载，检查 CPU、RAM、GPU、VRAM、磁盘和各阶段等待。发现明确瓶颈后，只改变一个因素，再用同一输入复测。

| 设备档位 | 可作为何种候选 | 先测什么 / 升级触发 | 当前限制 |
| --- | --- | --- | --- |
| CPU 保底 | 界面验证、离线回放、小样本、故障诊断和人工流程 | 核实实时 YOLO 是否能满足目标周期；如 CPU 持续饱和或候选严重延迟，再评估 GPU 主机 | 不承诺 CPU 具备实时多路容量；模型和检测可能争用 CPU |
| Apple Silicon | Mac 开发、macOS 捕获权限/界面验证、有限的本机模型对照 | 记录统一内存压力、MPS/CPU 实际设备、采集与录像时延；只有测到模型资源为瓶颈才评估模型量化或独立推理机 | 不代表 Windows/CUDA/Seetong 通过；实时检测权重及 MPS 结果仍须按本机哈希和输出验证 |
| NVIDIA 8 GB | 约 8 GB VRAM 的单机验证基线；原设计提及 RTX 4060，但其目标现场尚未验证 | 记录 YOLO 与本地 VLM 同时运行的显存峰值/余量、OOM、候选队列龄和端到端时延；显存压力证据稳定后再比较 12–16 GB | `NOT_BENCHMARKED`；不能据卡名、显存或一次截图承诺 10 路 |
| NVIDIA 12–16 GB | 解决已测得显存余量不足的单机候选 | 同一负载检查是否减少 OOM/超时、端到端 p95 是否改善；若瓶颈在采集或 worker 队列，停止 GPU 升级假设 | 显存增加不自动增加并发；软件当前模型槽仍固定为 1 |
| NVIDIA 24 GB 及以上 / 第二主机 | 在单机基准证明 GPU 饱和后做高显存或任务拆分对照 | 先证明单机推理饱和，再在同模型、同输入、同截止下测任务拆分、网络/IPC、故障恢复、两机离线运维 | 不在多卡之间拆分一个大模型作为默认路线。先考虑完整模型实例放在第二推理主机；须测网络与故障边界 |

以上档位是测试分层，不是采购建议。价格、吞吐和所需路数依驱动、模型、客户端、码率、输入像素和并行负载变化，本章不给价格或性能保证。升级前需由操作员把测量填入[模板](../templates/device-benchmark.template.json)，并由独立复核者确认瓶颈和回退条件。

每种升级都按同一顺序过门：先复现 8GB 基线并保留配置/证据副本；然后只改一个变量；依次检查采集新鲜度与身份、告警/证据持久化、CPU/RAM/GPU/VRAM 余量、正例/困难负例/unknown 留出集准确性、15–30 分钟现场试跑和长稳。Apple Silicon 还要单测 ScreenCaptureKit 权限与统一内存；12–16/24 GB 只在测到 GPU 或显存瓶颈后比较；第二主机还需加入网络中断、模型退出和故障恢复。只有相关门禁逐项通过才可保留更高输入清晰度、频率或替换模型。任一 Gate 失败即停止该阶段、把受影响区间标 unknown、保留失败证据，并恢复上一个已验证配置、路由和设备路径。硬件建议不会替代这些门禁。

| 升级候选 | 每次只增加的内容 | 保留原 8GB 基线后必须通过 | 失败时回退 |
| --- | --- | --- | --- |
| Apple Silicon | 单独验证 Mac 捕获/统一内存，模型对照使用 `existing` | 捕获新鲜度、告警/证据读回、资源余量、同一准确率留出集、现场时长 | 停止复核或恢复上一配置；保留 unknown 和失败日志 |
| NVIDIA 12–16 GB | 先维持输入不变，再单独增加某一频率/清晰度或替换模型 | 资源峰值、端到端时延、队列龄、准确率和现场长稳无退化 | 撤销唯一改动并恢复原路由/配置 |
| NVIDIA 24 GB 或第二主机 | 只在测到单机瓶颈后试更大模型或将完整模型实例放到第二主机 | 加入网络故障/断连/恢复测试，重复采集、证据、准确率和持续运行 Gate | 关闭该候选路由，退回本机已验证配置；未知区间保留待人工处理 |

这些是人工配置与验收步骤，不含自动扩容、自动升档或跨卡拆分模型功能。

## 模型更换、执行后端与准确性

将任务拆成两类后分别验证。YOLO11n 权重承担实时目标检测，是 runtime 的必需依赖；更换检测权重可能改变目标框、轨迹和候选数量，必须重跑检测精度、资源与全流程测试。Ollama/OpenAI-compatible 视觉模型是可选的候选复核器；更换它会改变 supported/dismissed/uncertain 比例，必须用固定的正例、困难负例和未知场景留出集重验。更换提示词也会影响输出，不能仅因 JSON schema 通过就视为语义准确。

模型路由当前是“一个主服务、最多一个备用服务、一个在途请求”的保守入口，不是可热切换的模型管理器。单并发只约束本客户端请求，不能单凭 HTTP 连接取消就证明后端 GPU 已停止。超时或发送后的连接状态不确定时，当前路由停止自动切备用，并保持熔断、后续复核返回不可用；先核实本机后端已经停止或重启后端，再重启应用。确认未连上的服务或完整错误响应才允许在剩余时间内尝试备用。网关另有 2 秒请求接收期限，防止不完整请求长期占槽。建议在隔离配置副本中改路由，先用不含现场身份的本地样本，保留旧 routes、配置、权重和运行目录；失败即停复核并保留未知候选，回到上一份经验证配置。禁止以放宽 15 秒上限、抬高并发、删除超时样本或云端上传画面来制造通过结果。

本地路由文件字段按[路由配置示例](../templates/local-model-routes.example.json)填写，并复制到仓库外的隔离目录。示例中的端口、模型 tag 和备用端点是占位选择，必须逐个核实本机服务、视觉能力、tag 和许可后再改；不要原样启动。Ollama 官方文档列出本地 [`/api/chat`](https://docs.ollama.com/api/chat) 与 OpenAI-compatible vision chat 接口；协议兼容不保证每个模型都支持本项目所需的视觉输入和结构化结果。使用 `protocol: "openai"` 时 endpoint 必须是本机 loopback 服务的 base URL（可含 `/v1`），不能指向公网 API。

目标工作站启动示例：

```powershell
.\scripts\start-desktop.ps1 -PythonPath .\implementation\.venv\Scripts\python.exe -ResourceProfile existing -ConfigPath .\runs\model-trial\config.json -DataDir .\runs\model-trial\data -Source demo -Routes D:\approved-local\routes.json -EnableReview
```

Mac/Linux：

```sh
FACTORY_MONITOR_PYTHON="$(pwd)/implementation/.venv/bin/python" ./scripts/start-desktop.command --resource-profile existing --config "$(pwd)/runs/model-trial/config.json" --data-dir "$(pwd)/runs/model-trial/data" --source demo --routes /approved-local/routes.json --enable-review
```

仅在已按[8GB 手册](13-8gb-and-capture-isolation.md)完成固定模型预热、并使用单路模板时才省略 `-ResourceProfile` / `--resource-profile` 使用默认 `vram8gb`；相应命令还需指定 `templates/8gb-model-routes.example.json`。默认配置不含自动切换到高配的逻辑。

先以 `demo` 或本机固定测试样本验证路由状态和错误路径，禁止未经单独授权将生产 live 画面指向非本机 endpoint。不要将 routes 文件、令牌或本地地址以外的敏感配置提交到仓库。路由尝试元数据不会记录图片内容，但仍须按本机保留要求保护应用数据。

ONNX Runtime 的 Execution Provider 可针对不同设备选择执行后端，但它不是当前项目已接入的 YOLO 推理实现。CUDA、DirectML、CoreML 是否适用取决于模型导出、算子覆盖、运行库、驱动和依赖版本；未来做后端移植时必须锁定兼容组合并在目标机器实测。ONNX Runtime 官方说明了[Execution Provider](https://onnxruntime.ai/docs/execution-providers/)、[CUDA 兼容条件](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html)、[Windows DirectML 状态与限制](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html)和[macOS CoreML 打包条件](https://onnxruntime.ai/docs/execution-providers/CoreML-ExecutionProvider.html)。不要把这些文档写成当前桌面程序已经支持的选项。

## 升级门槛与回退

进入候选升级前先备份并哈希旧版代码/配置/模型，使用全新版本目录、独立配置副本和全新数据目录。配置和事件库可能随版本变化，不能把新版本数据库直接覆盖到旧版目录。先运行合成验证，再用固定本地留出样本，最后才按[阶段验收](05-validation-and-acceptance.md)在现场逐路验证。任一错绑、候选丢失、证据路径越界、超时被显示为正常、崩溃后数据库异常或停止无法确认，都停止扩大路数；隔离新目录和原始记录，恢复上一版完整程序与它匹配的数据副本。旧版的未复核证据须先人工归档并核对，再按现场留存规则处置。

不得把模型输出作为违法、犯罪或主观意图判断。屏幕只表述可见候选、模型状态和证据；“人工确认候选”也是操作员标签，不是法律结论。
