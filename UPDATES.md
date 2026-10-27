# 夏鱼洛 App 内更新

当前源码0.1.16 / Android versionCode17。仓库已公开，Windows测试安装包已构建，但没有正式签名 Release；按所有者要求暂缓发布配置，**不绕过校验、不更换原签名**。公开源码、构建产物、正式更新与真机验收不是同一状态。

<a id="015-客户端完善与发布阻塞"></a>

## 0.1.15 客户端完善与发布阻塞

保留此标题供旧链接定位。0.1.15补齐客户端可靠性，0.1.16继续保护云同步的更新启动窗口：

- Windows 在线/离线包均复核实际 `build-info.json`、签名版本和资产哈希，界面展示训练备份、下载字节、校验、重启阶段；文件下载完成不等于安装成功。
- helper 来自新签名包，等待旧进程退出后同盘替换，要求新版本返回匹配 nonce、版本、本地服务及原生窗口就绪 ACK。超时/立即退出时仅停止本次持有的进程并尝试回滚，不猜PID或杀其他程序；独立子进程锁文件时保留旧版备份并报错。
- helper 完成健康判断、写成功结果后，用同 nonce/version 的 `health-accepted.json` 解锁云导入与任务执行，避免仍可能回滚时改写旧版不理解的数据；确认失败则云功能等待手动重启，原数据/旧版备份保留。
- Android 支持取消、验证缓存复用、重开系统安装器和未知应用权限入口；离开组件不更新旧界面，返回 App 不代表安装成功。

[实际 check-only 预检](https://github.com/XiaYuLuo22/xiayuluo/actions/runs/40276737781)明确缺少3项 Actions Secrets：`XIAYULUO_RELEASE_PRIVATE_KEY`、`XIAYULUO_ANDROID_KEYSTORE_BASE64`、`XIAYULUO_ANDROID_STORE_PASSWORD`。所有者应使用原有材料在 [Actions Secrets](https://github.com/XiaYuLuo22/xiayuluo/settings/secrets/actions)安全配置，不把值发到聊天、源码、日志或公开仓库。

`release.yml` 的 `check_only` 只检查存在性，禁止后续构建/发布，不创建标签；通过不代表密钥匹配。源码推送只触发独立 Windows 构建，不自动设置 Secrets 或发布 Release。不能移动旧标签、换密钥或反复重发旧包掩盖失败。

## 使用方式

- **Windows发行版**：设置 → 版本与更新 → 下载更新。先完成/停止聊天和Codex；点击后检查固定签名源、下载校验、重启替换，保留原安装位置。启动及每小时可见空闲只检查，不静默安装或降级。
- **Android**：设备 → 版本与更新 → 下载更新。不依赖配对电脑在线；系统要求时授予安装未知应用权限，最终仍需本人确认覆盖安装，不卸载旧版。安装成功以重启后的真实版本为准。
- **Windows离线包**：设置 → 版本与更新 → 选择更新包并重启，由原生文件选择器选择 `.xyupdate`；取消保留当前版。HTTP不接受任意文件路径，手机/源码版不能使用本机导入。
- **源码/iOS**：源码可检查版本但不能自覆盖checkout、`.venv`或配置；需手动更新源码或先装Windows发行版。iOS尚无此安装更新流程。

无新版、未发布、网络失败或校验错误时保留当前版并显示真实原因，不回退GitHub网页、不冒报“已是最新版”。旧包的按钮不能被云端热替换，首次须手动取得支持该能力的新安装包。签名更新不是Windows Authenticode，不消除系统SmartScreen。

## 更新时保留训练数据

训练根独立于安装/源码，Windows默认 `%LOCALAPPDATA%\Xiayuluo\training`，程序可安装到 `E:\Xiayuluo`。首次可选独立绝对根，位置跨版本登记；登记失联、新配置冲突或格式损坏时停止训练/备份/更新，不新建空数据代替。旧 `dist/` / `.vision-learning/` 文件不自动迁移或删除，详见 [TRAINING_DATA.md](TRAINING_DATA.md)。

App内更新遇到已有训练数据时先取得跨进程写锁，建立含datasets/models/checkpoints的快照并校验SHA-256；锁/空间/内容变化/校验失败，或超过10,000文件/8GiB时中止更新，不以部分备份冒充成功。helper、程序回滚和卸载均不操作训练根。

直接运行 `Xiayuluo-Setup.exe` 只检查目录隔离，**不调用Python自动备份**；手动覆盖前须在App或CLI备份并校验。历史备份不自动清理，最近成功备份仅本次运行记录。明文同盘备份不防硬盘损坏、恶意软件或手删，应另做独立磁盘加密备份。恢复只写不存在的新目录，不覆盖原根或自动改登记。

## 云端更新何时可见

发布流程：递增版本/Android versionCode → 测试 → 双端构建 → 原签名核验 → 签署清单 → 上传完整草稿 → 转正式版 → 匿名下载复核。只有全部成功，App才有可下载安装的新版本。

固定清单地址为 `https://github.com/XiaYuLuo22/xiayuluo/releases/latest/download/update.json`，当前仍是尚未正式上线的目标，不从聊天或源码分支接收下载脚本。私有仓库匿名地址不可用，不能把GitHub token内置App；也不擅自更改仓库可见性或公开私人数据。云同步服务、模型提供者与Release更新是不同链路，见 [CLOUD_SYNC.md](CLOUD_SYNC.md)。

## 开发者一次性发布配置

1. `assistant/update-config.json` 与 `mobile/src/update-config.json` 必须一致。固定Ed25519公钥不随意更换；保留忽略提交的 `.release-signing/release-private.pem`，安全独立备份。
2. 复用 `.android-signing/xiayuluo-private.p12` 与原密码，包名固定 `com.xiayuluo.assistant`，实际证书SHA-256须为 `bef9a3315531035f91362a72fd3012198304fd83d21d07009d67fd1df90ac96a`，不能改用debug key。
3. 通过仓库安全设置配置上文3项Secrets，不在工作流写值、不提交配置/数据库/私钥。清单私钥必须匹配固定公钥。
4. 发布时同步更新 `assistant/__init__.py`、`pyproject.toml`、`mobile/package.json`、`mobile/app.json`、npm锁文件和 `RELEASE_NOTES.md`，Android versionCode递增。在已测试的 `vX.Y.Z` 标签运行完整工作流，不移动旧标签。
5. 签署前再次核验APK实际证书、包名、版本/版本码与Windows build-info；发布后匿名下载清单及两资产，核验签名、字节、SHA-256和实际包。失败将本次新Release退回草稿，保留旧版。

本人私下生成Windows离线包的示例（不上传、不发布、不换密钥）：

```powershell
python tools/release.py windows-package --version 0.1.16 --windows Xiayuluo-Windows.zip --notes-file RELEASE_NOTES.md --key-file .release-signing/release-private.pem --output Xiayuluo-0.1.16.xyupdate
```

输入须为对应版本构建，输出已存在则拒绝覆盖；包只含签名清单与程序ZIP，不含私钥或用户数据。

## 安全与恢复边界

- 清单为 `{payload: "原始JSON文本", signature: "Ed25519十六进制签名"}`，先校验原始UTF-8签名再解析。校验应用标识、仓库/标签、版本、大小与SHA-256，拒绝降级；清单最大32KiB、安装包最大512MiB。
- 下载保留TLS并逐跳限制官方发布资产域名，不携带App/GitHub凭据。Windows拒绝路径穿越、链接、重复路径、设备名、用户数据和解压超限；下载/准备失败只清理自有临时文件。
- 安装目录含配置/数据/训练/Codex工作区或 `.env`、`.assistant`、`.venv`、`.git` 时拒绝替换。目录不可写、占用、空间不足时保留当前版并报错，不管理员提权。
- Windows保留同级 `.Xiayuluo.backup-*` 旧程序目录；它不是数据库备份。helper运行时不可清理临时 `xiayuluo-update-*/job.result.json` 或旧版备份。程序目录回滚不能回退数据库，schema变更需独立事务/备份/恢复验证。
- Android仅向缓存下载，校验大小和原生SHA-256后交系统，系统复核APK签名/版本；失败只清理对应新缓存，已验证缓存重开安装器前再次核验，不绕过权限。
- 升级保留外部配置、聊天、四级记忆、稳定同步ID/导入日志与训练目录。不要以删除副本/重建空库排错，云数据保护细节见 [CLOUD_SYNC.md](CLOUD_SYNC.md)。

## 历史行为与验证状态

0.1.1开始支持签名清单；0.1.3增加下载按钮，当时的浏览器回退已被0.1.11替代；0.1.9增加图形安装器、自动检查与本机离线包；0.1.13增加训练数据保护；0.1.15/0.1.16完善健康确认与云写入屏障。旧“私有仓库/跳网页/逐次批准”说明不覆盖当前行为，版本摘要见 [RELEASE_NOTES.md](RELEASE_NOTES.md)。

Windows测试安装包已成功构建，回归覆盖签名/篡改、版本/URL/大小/哈希、重定向、解压保护、目录保留、启动失败/回滚、鉴权、更新期间阻止新任务与训练数据保留；手机协议/TypeScript与脚本导出通过。尚未用户Windows/Android真机覆盖升级验收，不能保证任意系统/网络/故障状态下更新必定成功。实际Android包记录见 [ANDROID_INSTALL.md](ANDROID_INSTALL.md)，独立构建不等于正式在线发布。
