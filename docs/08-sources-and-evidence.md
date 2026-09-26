# 来源、版本与证据索引

## 1. 三层版本不能混淆

|层次|标识|证明什么|
|---|---|---|
|原项目冻结源码|`260862912d7386642aa538ac49d72442b9d673bb`|本仓 `implementation/` 的来源；129个文件有逐一SHA-256|
|本仓库版本|根目录 `VERSION`，Git提交，Release标签|当前执行手册、检查器、发布包与固定源码的组合|
|现场实际版本|现场记录中的源码/配置/依赖/模型哈希|真正被测机器运行了什么；不能用前两项代替|

[原项目提交](https://github.com/wailliamkf-commits/factory-monitor/tree/260862912d7386642aa538ac49d72442b9d673bb)与[本地源码清单](../evidence/implementation-manifest.json)对应。运行 `python scripts/verify_repository.py` 会重新比对文件内容；本地生成的环境、配置和录像不进入源码清单。

`implementation/` 保留历史上下文，不直接编辑。若后续集成新功能，应在开发分支完成代码与测试，再经过审查更新整份快照、来源提交和清单；禁止只改一个哈希来掩盖来源差异。

## 2. 现有证据

|证据|范围|结论与限制|
|---|---|---|
|[原实验与日志](../evidence/source-2608629/README.md)|开发Mac合成输入、固定源版本|275项测试；7项主动观察实验；不含现场画面|
|[原双平台CI](https://github.com/wailliamkf-commits/factory-monitor/actions/runs/36091225947)|托管Windows/Mac、固定源版本|272/274项通过与平台跳过；合成负载和构建通过|
|[组件与Qwen实测](../implementation/docs/MAINLAND_EXECUTION_20260925_zh.md)|M5公开公交静帧裁剪|共享检测时耗下降；Qwen负例误报，十候选15秒仅2完成|
|[独立审查修复记录](../implementation/docs/ACTIVE_OBSERVATION_VERIFICATION_20260925_zh.md)|4个隔离模块、7个复现问题|问题关闭并重跑；没有自动点击或现场验收|
|[新目录首次本机检查](../evidence/blueprint-initial/README.md)|新副本、Mac既有依赖、合成输入|274通过/1跳过；新实验7项通过，现场NOT_TESTED|
|本仓库 Actions 与 Release|本仓最新提交|需核对对应提交SHA；当前发布结果以 Actions 和 Release 记录为准|

原始云端日志随仓保存，`ci-final.json` 保留原日志哈希与URL。它们没有生产图片和现场账号。新仓的 CI 不会访问摄像头，只有代码、合成实验与工程构建。

新 CI 会将合成 JSON/Markdown 报告保存为每平台 `synthetic-validation-*` Actions artifact，默认保留14天。对应运行页面可下载；长期交付须另存到经审查的证据目录或Release，不能把临时artifact当永久存档。失败运行也尝试保留已生成报告；缺失报告仍视为缺失，不能补写成功。

## 3. 已核研究与引用方式

研究材料按 2026-09-25 核查状态保存；软件版本、仓库内容和硬件价格会变化。以下采用“已核公开事实 / 架构推断 / 现场待测”的区分，不把作者测量当成本项目实测。

- [单屏主动巡视与国产GUI模型研究](../implementation/docs/ACTIVE_VIEW_RESEARCH_20260925_zh.md)：Qwen、UI-TARS、PaddleOCR、Microsoft UIA、Seetong主/子码流。重点是动作建议、轻量界面核对和真实源像素验证。
- [异构PD及Frigate/go2rtc审查](../implementation/docs/HETEROGENEOUS_STABILITY_20260925_zh.md)：检查用户建议仓库的实际内容、vLLM/SGLang官方部署边界、摄像源流复用与平台限制。
- [大陆部署与VLX-Seek/Laya研究](../implementation/docs/MAINLAND_RESEARCH_20260925_zh.md)：原始选型依据。历史价格不是今天的采购报价。
- [既有架构研究](../implementation/docs/ARCHITECTURE_UPGRADE_RESEARCH_zh.md)：保留原调查与失败证据，后续以版本和场景重新验证。

对应官方入口：

|用途|一手来源|本项目采用边界|
|---|---|---|
|GUI模型与视觉模型|[Qwen3-VL](https://github.com/QwenLM/Qwen3-VL)、[UI-TARS](https://github.com/bytedance/UI-TARS)|能力候选，不据公开榜单承诺本站点击或行为准确率|
|界面核对|[PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)、[Windows UIA](https://learn.microsoft.com/windows/win32/winauto/uiauto-providersoverview)|现场控件是否可见、OCR能否可靠读小字需另测|
|PD分离|[vLLM](https://docs.vllm.ai/en/latest/features/disagg_prefill/)、[SGLang](https://docs.sglang.ai/advanced_features/pd_disaggregation.html)|只在硬件拓扑与实测瓶颈支持时做对照|
|用户建议实验|[固定快照327c326](https://github.com/Soulmate-Halo/heterogeneous-gpu-pd-lab/tree/327c326d85170921113607750e4a3a1a320a9335)|研究实验记录；未复制未公开服务实现|
|可选源流路线|[Frigate](https://github.com/blakeblackshear/frigate)、[go2rtc](https://github.com/AlexxIT/go2rtc)|有经授权源流时才验证；目前没有接入|

## 4. 模型和依赖许可状态

原有实现使用 PySide6、OpenCV、PyTorch、Ultralytics、Ollama 与 Qwen 等。模型/组件的授权和分发条件不能由“都能从互联网下载”推导。特别是原研究记录 Ultralytics 的AGPL/商业许可问题；商业分发前必须按实际版本、打包方式和许可证完成审查。本仓不复制这些权重或二进制，也不声称完成商用许可清理。

第三方研究只保留来源链接和本项目分析；没有授权明示的第三方仓库不作为可任意再分发的代码库。仓库可见性本身不构成第三方商用授权声明。

## 5. 新证据怎么记录

每次运行至少保存：代码提交、配置指纹、模型/依赖版本、平台、输入来源synthetic/onsite、起止时间、原始输出、超时/丢弃/失败计数、录制缺口、指标口径和未测项目。用[现场模板](../templates/field-run.template.json)起草记录，原始生产素材只留现场。修复前后的对比必须使用相同输入和门槛，不能删除失败请求再计算延迟。
