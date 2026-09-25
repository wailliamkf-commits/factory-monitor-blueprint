# 模型与完整资源索引

用户要求的模型及依赖以同一私有仓库的 **Release 实体附件** 交付，不只有模型链接。下载 [v0.1.0-preview](https://github.com/wailliamkf-commits/factory-monitor-blueprint/releases/tag/v0.1.0-preview) 的全部 7 个分片，按[还原步骤](../docs/09-delivery-and-offline.md)恢复原始 Windows ZIP。

|内容|固定版本与用途|恢复后位置|
|---|---|---|
|Qwen3-VL|`qwen3-vl:2b-instruct`，Q4_K_M；候选复核基线|`resources/models/ollama/`，完整 manifest + 全部 4 个唯一内容 blob|
|YOLO|`yolo11n.pt`；人员检测基线|`resources/models/yolo11n.pt`|
|Windows 本地模型服务|Ollama 0.34.2 官方归档|`resources/ollama/`|
|Python / 运行库 / Git|Python 3.12.10 x64、VC x64、MinGit 2.55.0.5|`resources/` 与 `resources/git/`|
|Python 依赖|55 份归档，包含 PyTorch 2.11.0+cu128|`resources/wheelhouse/` 与带哈希锁文件|
|候选安装与检查|已有独立 Windows 候选及 1–6 步入口|恢复包根目录；源提交 `1f6f242`|

- [模型和包身份](model-registry.json)：体积、完整 ZIP 哈希、每个模型文件的哈希、未通过项。
- [精确重建配方](rebuild-recipe.json)与[小文件种子身份](rebuild-seed.json)：维护时从固定官方来源恢复同一分片，任何大小或哈希不符即停止。
- [Release 分片清单](windows-release-parts.json)：7 个实际附件名称、大小和哈希，整体恢复结果的固定身份。
- [完整资源清单](offline_bundle_manifest.json)：107 个 payload 的体积和哈希；自身是第 108 个文件。
- [资源来源记录](RESOURCE_PROVENANCE.json)：来源、供应方校验范围和已知限制。
- [资源包源码版本](SOURCE_VERSION.json)：包内候选的旧提交，不能与本仓 `implementation/` 混为一版。

Qwen 保留原许可 blob；Windows 发行归档、wheel 保留各自许可文件。YOLO/Ultralytics 使用 AGPL-3.0 或另行商用授权，本私有仓交付用于内部工程验证，不能解释为已解决闭源产品授权。部署时以固定版本的原许可证为准。

模型权重可跨平台复用；CUDA wheels、Ollama Windows 二进制和捕获组件不能跨平台复制。Mac 仍需[本机运行环境](../docs/03-macos-execution.md)。GPU 驱动、监控客户端及账户、现场标定和真实影像不属于这些资源。

本次交付“全量”是指已列明的固定模型/资源集合完整，不代表所有模型、所有监控软件或两平台全离线安装器。Qwen 既有负例误报与十事件时限测试失败仍保留；模型文件齐全不能抵消这些结果。软件调教按照[软件适配流程](../docs/10-client-learning.md)另行完成，当前状态为 `NOT_TESTED`。
