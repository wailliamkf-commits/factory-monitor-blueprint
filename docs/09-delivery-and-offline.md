# 全量资源发布、还原与离线交接

## 1. 同一 Release 的两套内容

源码、桌面扩展与演示见 [v0.2.0-preview 发布页](https://github.com/wailliamkf-commits/factory-monitor-blueprint/releases/tag/v0.2.0-preview)；完整模型和 Windows 依赖分片仍在 [v0.1.0-preview 资源发布页](https://github.com/wailliamkf-commits/factory-monitor-blueprint/releases/tag/v0.1.0-preview)。两个发布页各自有 SHA256SUMS.txt，应放在分开的下载目录校验。仓库当前公开；现场数据、凭据和本地配置不在发布包内。

|附件|包含什么|用途|
|---|---|---|
|`FactoryMonitor-Blueprint-v0.2.0-preview.zip`、`PACKAGE_MANIFEST.json`、`SHA256SUMS.txt`|当前完整手册、固定核心、桌面扩展、合成实验、测试、适配模板和证据|架构与开发交接|
|`part-000001-of-000007.bin` 至 `part-000007-of-000007.bin`|完整原 Windows 离线资源 ZIP 的原始字节，未删减模型|全部下载后还原 6,637,814,564 字节的原 ZIP|
|`FactoryMonitor-Windows-20260925.zip.parts.json`|分片顺序、大小、每片哈希及整体哈希|只读验证、完整性检查与还原|
|`Restore-OfflineBundle.ps1`|Windows PowerShell 5.1 还原器|尚未安装 Python 也可先恢复离线包|

GitHub 官方要求[每个 Release 附件小于 2 GiB](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)。因此使用 1 GiB 分片，而非把 6.6GB 放进 Git 历史。**模型和依赖确实包含在分片中**；GitHub 自动生成的 `Source code (zip)`、Blueprint ZIP 与完整离线包用途不同，不能少下分片。

总下载量约 6.64GB，保存分片、还原 ZIP、解压及安装需要多份空间。准备至少 50GB 空闲；录像容量需另按路数、码率和保存天数计算。还原位置需支持大于 4GB 的单文件，例如 NTFS 或 APFS，不能使用 FAT32。

GitHub 分发不保证中国大陆现场连通性。可在获准的准备机下载、校验后通过移动硬盘或内网转交；完整本地 AI 运行不依赖 GitHub。监控客户端自己的摄像头网络需在现场单独验证。

## 2. Windows 尚未安装 Python：先还原资源

将 7 个分片和 `.parts.json` 放到同一个新目录，例如 `D:\FactoryMonitor-Download`；从同一 Release 下载 `Restore-OfflineBundle.ps1` 放在该目录。不要改分片文件名，不要尝试逐片解压，不要用文本命令拼接二进制。

在 PowerShell 进入下载目录，先创建一个独立的空还原目录，再运行：

```powershell
cd D:\FactoryMonitor-Download
New-Item -ItemType Directory -Force D:\FactoryMonitor-Restored | Out-Null
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Restore-OfflineBundle.ps1 -ManifestPath .\FactoryMonitor-Windows-20260925.zip.parts.json -OutputFile D:\FactoryMonitor-Restored\FactoryMonitor-Windows-20260925.zip
Get-FileHash D:\FactoryMonitor-Restored\FactoryMonitor-Windows-20260925.zip -Algorithm SHA256
```

脚本逐片核对大小与哈希，然后检查完整 ZIP；目标已存在、缺片、编号缺失或损坏会失败（清单中的行顺序可变，按文件编号还原），不覆盖已有包。执行策略只针对本次进程；脚本不会安装软件、启动监控或联网。整体 SHA-256 必须为：

```text
4652f9677df0e97f48be8b3da82d7a9db7b07a3b1bdcdeb70e1497a2fa92a4a1
```

结果不一致就停止，重下报错的分片；不要绕开校验。确认后用 Windows 文件管理器“全部解压”到新的目录，读根目录 `先读我.md` 和 `给Windows执行者.md`。资源包有 108 个文件，逐文件清单位于包内 `offline_bundle_manifest.json`。

## 3. 已有 Python 3.12 的 Windows / Mac：还原与复核

Python 还原器使用硬链接原子提交结果，输出目录须位于支持硬链接的本机 NTFS/APFS 文件系统；不要直接输出到 exFAT 移动盘，可先在本地还原再复制。

先解压 Blueprint ZIP，进入它的根目录。所有下载附件位于 `downloads/`，在根目录新建 `restored/` 后执行（Windows 可将 `python` 换成 `py -3.12`）：

```sh
python scripts/bundle_parts.py verify --manifest downloads/FactoryMonitor-Windows-20260925.zip.parts.json
python scripts/bundle_parts.py join --manifest downloads/FactoryMonitor-Windows-20260925.zip.parts.json --output restored/FactoryMonitor-Windows-20260925.zip
python scripts/verify_offline_zip.py restored/FactoryMonitor-Windows-20260925.zip
```

最后一步核对固定整体 SHA-256、ZIP 中的完整资源清单、107 个文件和 Qwen 内容引用。脚本只读，不安装资源、不解压到系统路径、不运行模型。此步骤可能持续数十秒或更久；哈希检查占磁盘读取时间，等待结果，不把暂无输出当卡死。

Blueprint 本身的校验：Windows 用 `Get-FileHash` 对照 `SHA256SUMS.txt`；Mac 在 ZIP、外部 manifest 与校验文件都齐全时执行：

```sh
shasum -a 256 -c SHA256SUMS.txt
```

解压 Blueprint 后执行 `python scripts/verify_repository.py`；它核对固定源码、文档、模板与包内清单。内容一致只证明交付完整，不证明现场性能。

## 4. 恢复后按什么顺序执行

完整资源包固定包含 Python 3.12.10 x64、VC、MinGit、Ollama 0.34.2、Qwen3-VL 2B Q4_K_M、YOLO11n 以及 55 份 Windows 依赖资源。每个模型文件与来源见[资源索引](../resources/README.md)。

按 `1-prepare.cmd` → `2-model.cmd` → `3-preflight.cmd` → `4-capacity.cmd` → `5-monitor.cmd` → `6-collect.cmd` 执行；每一步的前置条件、路径输入、失败处理见[Windows 手册](02-windows-execution.md)。准备脚本只在原修复项目旁建立候选，补丁不匹配即停，不能覆盖现场项目；不能对已包含补丁的源码重复应用同一补丁。`2-model` 使用本地资源而不是在线拉取权重。

**版本必须分开：**完整资源 ZIP 的候选源码为 `1f6f242`；本仓 `implementation/` 为 `2608629`，新增主动观察模块尚属隔离实验。不要直接替换原包源码后声称哈希仍有效。若要用本仓新版运行时，需要在独立开发候选中显式升级，并重新做[验证与验收](05-validation-and-acceptance.md)，保留原 Windows 修复。

模型文件可供 Mac 复用，Windows Ollama、CUDA wheels 与 WGC 不能用于 Mac；Mac 依赖和原生采集仍按 [macOS 手册](03-macos-execution.md)准备。本次没有交付 Mac 全离线安装器、NVIDIA 显示驱动或监控软件安装器。客户端与 GPU 驱动在目标设备上另行准备并核验。

## 5. 按具体监控软件学习后才能进入现场

模型是通用基线。必须按[软件学习与调教](10-client-learning.md)建立“软件版本 + 窗口 + DPI + 布局 + 通道”的适配记录，通过一路、三路、十路和长测。图像、录像、账号、身份模板留在现场受控目录，不上传 GitHub。当前没有实际 Windows 的远程访问，不能将手册与模型齐全登记为已完成 Seetong 或其他软件适配。

## 6. 维护人员重新打包和发布

Blueprint：检查、测试并提交，工作区干净后，在 Git 克隆根目录执行：

```sh
python scripts/verify_repository.py
python -m unittest discover -s scripts -p test_bundle_parts.py -v
python scripts/build_release.py --output-dir dist
```

完整资源：只对已经审核且固定哈希的 ZIP 分片；生成目录必须不存在：

```sh
python scripts/bundle_parts.py split --input /path/to/FactoryMonitor-Windows-20260925.zip --output-dir dist/offline-parts
```

若资源内容升级，应建立新版本与新清单，再用冻结脚本 `implementation/scripts/offline_bundle.py --create ROOT --platform windows-x64 --python-version 3.12` 生成资源集合清单；不能沿用本次旧哈希。供应方归档、wheel 和模型许可证保持完整，商业交付许可另行核验。

首次发布完整资源时从 GitHub 重新下载全部附件，逐片校验、恢复完整 ZIP，运行 `verify_offline_zip.py`。后续只更新源码而资源哈希未变时，重新下载新增源码/演示附件逐项校验，引用既有完整资源读回结果，无须重复传输 6.6GB；从新解压的 Blueprint 运行仓库检查。发布记录必须列出对应提交、双平台 CI、下载回读结果和仍待现场验证项。不可用上传成功或哈希通过代替实际运行稳定性。


### 固定资源的云端逐字节重建（维护人员）

本仓同时保留 `resources/rebuild-recipe.json` 与 `rebuild-seed.json`。原资源 ZIP 使用存储模式，配方保存原 ZIP 头部/索引和每项内容的哈希；同一 Release 的 `resource-seed.zip` 仅保存 47 个原始小文件。61 个较大文件从固定的官方来源下载，每项都必须同时匹配原文件大小及 SHA-256，随后按原始字节顺序生成分片。云端不执行这些安装器、模型或依赖。

这条路径用于上行速度不足时完整复建，也使维护者可以审计固定模型的来源。它不把模型下载链接当作完整交付：最终仍须将全部 7 个实体分片上传到本 Release，而且整体和每片 SHA-256 必须与原包相同。任一来源失效或哈希变化都停止，不替换成新版本。

维护人员可在 GitHub Actions 手动运行 `Rebuild fixed offline resources`。流程校验小种子、运行恢复工具的测试，读取官方固定资源，只有全部原始字节校验通过后才上传分片。`resource-seed.zip` 是维护重建所用的附加文件；普通 Windows 使用者按前述步骤下载完整分片即可。云端重建不证明 Windows 安装、GPU 推理或现场验收。


### 独立 Windows 下载验收

`Verify full Windows release download` 工作流在新的 Windows runner 上从本仓 Release 重新下载全部分片，使用随包 PowerShell 5.1 还原器恢复 6.6GB ZIP，再检查整体哈希、107 个 payload、模型内容引用及 Blueprint 的每个文件。执行时分别指定 `blueprint_tag`（默认 v0.2.0-preview）与 `resources_tag`（默认 v0.1.0-preview）。Blueprint 清单提交必须与选中的源码 tag 完全匹配；不可拿旧包验证新提交。工作流不复用云端重建目录，也不安装、执行模型或读取摄像头。

成功报告以 `offline-release-readback` artifact 输出，维护者应保存到本 Release，避免只依赖临时 artifact 的保留期。它证明下载及 Windows 大文件还原完整，不证明离线安装、4060 推理或现场行为识别。经下载回读核验且确认远端资料完整后，可将本机重复打包/下载目录与原交付 ZIP 移入废纸篓，并保留源码工作区、现场修复目录及校验/清理记录。
