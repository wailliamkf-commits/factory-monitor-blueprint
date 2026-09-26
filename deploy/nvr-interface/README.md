# 录像机接口预案包

这是取流接入的准备材料和本机协议演练，**不是已经完成九路行为分析的安装包**。现场录像机型号、IP和流路径尚未取得；空配置不会连接任何设备。主方案见[接线与部署手册](../../docs/16-recorder-interface-deployment.md)。

## 包内内容

- [mediamtx.empty.yml](mediamtx.empty.yml)：MediaMTX **v1.21.1** 的本机空配置，只监听 `127.0.0.1:18554` 的 RTSP/TCP；禁用其他协议、管理接口、录制和发布。
- [接口演练脚本](../../scripts/rehearse_rtsp_interfaces.py)：限定本机的协议测试，不接现场设备、不读取用户视频、不调用AI模型。结果解释见[演练记录](../../evidence/interface-rehearsal/README.md)。
- [现场登记表](site-intake.template.md)：非技术人员先填第一页，技术人员再补实际接口；填好后留在现场，不上传公开仓库。

## 准备二进制与校验

MediaMTX 官方支持 Windows/macOS 独立二进制。[官方安装说明](https://mediamtx.org/docs/kickoff/install)；[固定 Release v1.21.1](https://github.com/bluenviron/mediamtx/releases/tag/v1.21.1)。本包不自动下载、不全局安装。

| 系统 | 官方资产文件名 | SHA256 |
|---|---|---|
| Windows x64 | `mediamtx_v1.21.1_windows_amd64.zip` | `faa97974861eb75a68b5aa326c78e7e7a6f670b5ef191bace78e715130381f23` |
| Mac Apple Silicon | `mediamtx_v1.21.1_darwin_arm64.tar.gz` | `25e20ed41611f1f3103b8359585210b29b11b69fa0d9e11bd11b92f7bbcb42ef` |

以上是本轮从 GitHub 官方 Release API 取得的资产 digest。下载后仍需计算本地哈希比对。Windows 使用 `Get-FileHash -Algorithm SHA256`；Mac 使用 `shasum -a 256`。哈希不一致或版本不符就停止。GitHub 当日大陆无VPN下载未验，现场可预先通过受控文件传递取得已校验资源，运行不依赖云端。

## 空配置启动检查

前置：已校验、解压对应平台的二进制；在本目录中操作；18554端口未被占用。以下路径按实际解压目录替换，示例目录本身不是已安装的位置。

Windows PowerShell：

```powershell
& 'C:\FactoryMonitorTools\mediamtx.exe' '.\mediamtx.empty.yml'
```

Mac：

```bash
/absolute/path/to/mediamtx ./mediamtx.empty.yml
```

预期：进程保持运行且无配置错误；没有任何摄像头画面是正常结果。仅本机可以访问18554；没有配置源，读取不存在的路径应失败。副作用只有本机监听端口和前台进程。按 Ctrl+C 停止，监听端口释放；不注册服务、不改防火墙、不改录像机、不录制。端口冲突时停止自己的测试程序或在私有配置里选择空闲端口，不结束其他程序。

## 取得厂家确认的一路地址后

先把空配置复制到受控、未跟踪的现场目录。由技术人员把 `paths: {}` 替换为 `cam01_sub` 和 `cam01_main` 两条路径，每条 `source` 填**厂家已验证的完整 RTSP 地址**，不是对URL模板猜参数。不能把本仓路径名当录像机的路径：前者是我们电脑内的别名，后者由设备定义。

这是语法说明，包含占位符，**不可直接启动**：

```yaml
paths:
  cam01_sub:
    source: '由厂家确认并在现场配置的低清RTSP地址'
  cam01_main:
    source: '由厂家确认并在现场配置的高清RTSP地址'
```

调用方以后读取的本地别名分别是 `rtsp://127.0.0.1:18554/cam01_sub` 和 `rtsp://127.0.0.1:18554/cam01_main`。代理默认保持已配置源连接，主流和子流各占一条上游会话；录像机原有客户端/录像会话另计。先只配一路的两个流，不直接同时创建十八条生产连接。[官方RTSP代理方式](https://mediamtx.org/docs/publish/rtsp-cameras-and-servers)

空配置的本机匿名读取只用于单用户隔离试验。共享电脑和正式后台部署需配置独立读取身份、限制配置文件访问，并将控制台日志留在受控目录；不要把含认证信息的上游地址放入聊天、命令行或公共Git。不能假设第三方错误日志会自动隐藏口令。Windows服务安装、Mac后台启动、录像保留和本项目分析消费者的衔接均尚未由本包自动实现。

停止代理并恢复空配置即可撤销本机接入。若试验改动了设备码流参数，须按现场记录由管理员恢复；本包不自动修改任何设备参数。需要清理时，停止相关进程后仅把本轮下载的临时二进制/压缩包移入废纸篓，保留源码、报告及用户原始文件。
