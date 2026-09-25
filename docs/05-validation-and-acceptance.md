# 验证与验收手册

本文定义从合成链路到现场验收的逐级放行门槛。阶段证据必须来自对应来源和目标设备；较低阶段通过不能替代更高阶段。

## 证据状态与当前结论

本仓库的 `implementation/` 是冻结源码快照。历史记录中的 CI 结果属于旧提交，不能移植成当前蓝图仓库的测试结论：原开发 Mac 单独记录 275 项通过；旧 CI 运行 `36091225947` 记录 Windows 272 项通过、3 项跳过，macOS 274 项通过、1 项跳过。该数字只说明当时 CI 对旧提交运行的自动化检查结果，不证明目标 Windows 客户端、RTX 4060、真实摄像头、当前新 GitHub 仓库或本手册已经测试。本仓库新提交须以自身 Actions 中对应提交的结果为准；云端新结果同样不构成现场验收。

本轮本地 Qwen3-VL-2B 的十候选并发测量为含排队 15 秒内 2/10 完成、8/10 超时；2 个已完成结果均把公开公交负例误报。它仍是失败证据，未被后续结构化输出、主动观察实验或异构 PD 讨论解决。更早的独立 runtime 合成烟测曾记录 1/10 完成、9/10 超时；这是不同运行，不要合并成一次测量，也不要只报告完成样本。所有超时、未知、错误和漏失都留在门槛分母中。见[大陆执行记录](../implementation/docs/MAINLAND_EXECUTION_20260925_zh.md)、[主动观察核验](../implementation/docs/ACTIVE_OBSERVATION_VERIFICATION_20260925_zh.md)和[冻结状态记录](../implementation/STATUS.md)。

人的行为只能由系统列出**画面中可见事实候选、时间与证据、模型不确定性**，再由人复核。不得把候选或模型描述写成偷窃、偷懒、动机、品德、违规结论或处分依据。画面不可观察、身份不符、画质不足时，结果为未知/需人工检查，绝不能当作“正常”或“无人”。

## 阶段门槛

| 阶段 | 输入与操作 | 通过条件 | 可声称的范围 |
| --- | --- | --- | --- |
| 0. 无屏幕合成 | 固定源码、隔离输出目录；使用 `demo` 合成源。先运行现有软件烟测，再分别观察录像/SQLite、错误和停止结果。 | 预期候选、事件状态、录像与预览可读回；完整查看媒体；错误和失败均保存；记录源码、环境和配置身份。 | 仅说明指定软件流程在该环境的合成输入上工作。不是采集、识别准确率、负例、真实客户端或现场验收。 |
| 1. 单路真实、被动观察 | 操作者在本地手动打开获准客户端，确认唯一测试路、真实客户端名称/版本、窗口、来源身份、采集权限和实际捕获后端；不启用生产点击。 | 保存校准前后截图/本地读回、实际时间戳、逐帧播放检查、起止时间和显式缺口；至少覆盖一次采集停止/恢复观察。来源、路号、ROI 或时间戳不一致即失败。 | 只限该机器、该客户端、该路和已验证画面条件。不得外推到其余九路。 |
| 2. 十路真实采集与候选 | 每路独立标识、网格 ROI、画面尺寸、显示缩放、帧新鲜度/heartbeat、可观测时段和缺口原因均经现场读回。先被动观察，再按单变量计划验证候选。 | 十路映射无错绑；窗口切换/遮挡/弹窗/缩放的情况有记录；盲区按区间并集计量；事件录像逐帧解码且时间戳审计；十个同时候选的队列终态全部可对账。任何丢帧、未知、未读回都保留为失败/缺口。 | 说明配置在这次限定运行中可观察到什么。十路在线或产生十条事件本身不证明识别准确、15 秒模型 SLA 或持续可靠性。 |
| 3. 真实 15–30 分钟演示 | 在目标 Windows 上通过被动采集和人工操作演示；演示前后都保留完整运行身份。包括真实无人时段、候选、录像回放、超时/不确定显示、人工确认与人工停止。 | 全程无隐式合成来源；至少一段候选和一段无事件时段都能按原始证据复核；录像、日志、DB、队列、人工标签相互对应；未完成项目现场展示为未知。任何安全或身份错配立即中止。 | 是目标配置的功能演示证据，不是 72 小时、正常班次误报率或现场 Gate PASS。 |
| 4. 独立样本与平台验收 | Windows 和 macOS 分开执行。`material_candidate`、`station_absence` 两类分别收集 candidate/final 阶段独立留出集，每类每阶段至少 50 正例、100 负例/混淆例；来源、真值、证据缺口、延迟和人工审阅者逐条记录。 | candidate recall ≥95%；final recall ≥90%；candidate p95 ≤3 秒；首次 review p95 ≤15 秒且包含排队；最终误报不超过每摄像头每 8 小时 1 次。超时、未知、错误、漏失和缺失延迟为失败样本。 | 仅对确实覆盖的任务、视角、时段、平台和数据条件作声明。独立审阅通过后，才可更新对应平台 Gate。 |
| 5. 72 小时稳定性 | 目标平台十路连续运行 72 小时；注入并记录冻结、断流、客户端重启、进程崩溃、模型超时、磁盘压力、主机重启、证据保留和峰值十事件突发。Windows、macOS 各自单独跑。 | 每路可观测覆盖、缺口、候选/模型终态、延迟原样本、队列账、录像逐帧解码、CPU/RAM/GPU/磁盘趋势、停止/重启退出码均可审阅；至少 100 次实际客户端切换循环且 ID 错绑为 0；恢复没有静默丢失。 | 只对通过的操作系统和确切配置报告结果。缺少任一关键记录、媒体不完整或恢复状态不明，Gate 保持 FAIL。 |

所有阶段统一填[现场运行模板](../templates/field-run.template.json)；客户端布局/能力和逐路标定另填[客户端能力模板](../templates/client-capability.template.json)。这些是供人工填写的蓝图模板，现有程序不会自动读取它们，也不会因此自动改变 Gate。

## 当前脚本、命令与边界

以下命令只供未来获授权的本机执行者使用。它们以隔离的新输出根为前提；合成 QA 会创建配置、数据库、媒体、预览和报告。不要把现场输出复制进本仓库。

在 `implementation/` 中先准备该实现要求的 Python 3.12 项目环境和依赖，再查看脚本参数。macOS：

```bash
cd implementation
.venv/bin/python scripts/qa_runtime_smoke.py --help
.venv/bin/python scripts/qa_ten_camera_smoke.py --help
.venv/bin/python scripts/qa_ten_camera_review_smoke.py --help
```

Windows PowerShell：

```powershell
Set-Location implementation
.\.venv\Scripts\python.exe scripts\qa_runtime_smoke.py --help
.\.venv\Scripts\python.exe scripts\qa_ten_camera_smoke.py --help
.\.venv\Scripts\python.exe scripts\qa_ten_camera_review_smoke.py --help
```

合成单路/多路软件流程和容量检查的实际入口为：

```bash
.venv/bin/python scripts/qa_runtime_smoke.py --root /ABS/PATH/qa-runtime --seconds 94
.venv/bin/python scripts/qa_ten_camera_smoke.py --root /ABS/PATH/qa-ten-camera --seconds 94
```

第一个使用一条明确合成路并检查证据与中断事件恢复标记；第二个仅允许 94–105 秒的十路合成证据容量检查，关闭模型复核，并生成十路合成媒体/SQLite/报告。两者都有写入副作用，目标目录必须不存在且位于仓库之外。它们不验证真实采集、模型复核 SLA 或现场准确率。故障时保留其原始目录和 stderr；不要删除失败证据后重跑来覆盖结论。恢复前先读回故障报告、进程退出和数据库/WAL 状态。

十候选模型烟测会向配置的本机 Ollama loopback 地址发起真实推理请求，创建合成候选、配置、SQLite 与报告；要求模型服务已由现场执行者明确启动，范围仅限合成输入：

```bash
.venv/bin/python scripts/qa_ten_camera_review_smoke.py --root /ABS/PATH/qa-review --seconds 31
```

它只接受 28–45 秒，不应把参数调到 15–30 分钟。若服务缺失、超时、输出格式错或误报，完整保留失败分母、请求/响应状态和报告。该脚本当前也不支持全程队列账、72 小时长测或现场验收。

真实标注集的现有评估入口见冻结的[验收协议](../implementation/docs/ACCEPTANCE.md)：

```bash
.venv/bin/python -m factory_monitor evaluate --input /ABS/PATH/cases.json --output /ABS/PATH/reports/evaluation.json --environment field
```

它写入评估 JSON 并校验样本、召回及测得的 p95；含超时/未知/漏失/错误、延迟非有限或样本不足时 Gate 失败。该命令不能证明实际客户端身份、采集权限、十路覆盖、切换正确性、72 小时运行或媒体完整性。字段含义以[案例模板](../implementation/scripts/acceptance/case-metadata.template.json)为准。这些脚本在导入阶段就需要已准备好的依赖，缺少 `cv2`/`psutil` 时连 `--help` 也可能退出；应先选用正确环境。

## Gate 判定规则

初始状态一律 `FAIL`/`NOT_TESTED`。只有相应目标设备上的原始证据、审阅记录、源码身份和所有门槛完整时，才能由独立审阅者逐平台更新。演示运行、合成脚本 `ok=true`、CI 通过、可打开的界面、模型输出合法 JSON、配置保存、运行了足够长时间或操作者口头报告，都不能单独改变现场 Gate。

参考：[实现验收协议](../implementation/docs/ACCEPTANCE.md)、[稳定运行与版本交付](../implementation/docs/STABLE_OPERATION_zh.md)、[中国大陆执行记录](../implementation/docs/MAINLAND_EXECUTION_20260925_zh.md)、[主动观察架构](../implementation/docs/ACTIVE_OBSERVATION_ARCHITECTURE_20260925_zh.md)。
