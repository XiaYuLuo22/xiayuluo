# 夏鱼洛 App 内更新

## 使用方式

支持更新的版本从 **0.1.1** 开始，始终可见的「下载更新」按钮从 **0.1.3** 开始。旧包必须先手动覆盖安装一次新版；云端修改不能让已安装旧包凭空获得能力。

### 0.1.3 下载更新按钮（兼容私有仓库）

- Windows：设置 → 版本与更新 → 下载更新，在系统浏览器打开 Windows 构建页面。登录有权限的 GitHub 账号，选择绿色成功的运行，下载 Artifacts 中的 `Xiayuluo-Windows`，解压后按 Windows 安装说明使用。失败或未完成的构建没有安装包；按钮本身不会修复构建。
- Android：设备 → 版本与更新 → 下载更新。没有检查到签名新版时，打开仓库 Releases 页面，登录后选择已发布的安卓 APK，再由系统确认覆盖安装。若尚无 Release，此页面没有可下载的新版，不表示检查成功或已安装。
- 两端按钮无需先成功读取公开更新清单；仓库私有导致清单 404 时仍可使用。不把 GitHub token 写进 App、链接或日志，不会改变仓库可见性。Android 已检查到有效签名新版时，同一个按钮改走下方校验安装流程。

### 签名清单安装流程

- Windows **发行版**：设置 → 版本与更新 → 检查更新 → 下载并重启更新。先完成或停止聊天/Codex。保持原安装位置，例如 `E:\Xiayuluo`；配置和数据库仍在 `%LOCALAPPDATA%\Xiayuluo` 或原先配置的数据目录，不被更新包覆盖。
- Android：设备 → 版本与更新 → 检查更新 → 下载更新。不依赖已配对电脑在线。若系统要求，允许“夏鱼洛”安装未知应用并重试，最终必须由用户在系统安装器确认。不要卸载旧版，覆盖安装保留配对。
- 源码版可以检查发布版本，但不能自动覆盖 checkout、`.venv` 或用户配置，需先安装一次 Windows 发行版。iOS 尚不支持此安装更新流程。

目前是手动检查、明确确认后更新，不是静默更新、强制安装或代码热替换。云端未发布、断网或签名失败会明确报错，不冒报“已是最新版”。

## 云端更新何时可见

修改代码不等于发布更新。发布者需要：递增版本及 Android versionCode → 测试 → 两端构建 → 签名清单 → 发布完整 Release。App 从固定 HTTPS 地址读取 `update.json`，不从聊天内容接收下载地址，不执行源代码分支上的最新脚本。

默认清单地址为 `https://github.com/XiaYuLuo22/xiayuluo/releases/latest/download/update.json`。它目前只是配置目标，**尚未正式发布或通过远程端到端升级验收**。清单式安装必须存在匿名可下载的正式 Release。私有仓库的匿名链接不可用；目前采用上方浏览器登录手动下载方式。若需私有仓库内直接校验下载而不经过浏览器，须另行增加私有更新服务，不能把 GitHub token 内置到 App。未经用户确认不得公开发布私人程序。

## 开发者一次性发布配置

1. `assistant/update-config.json` 和 `mobile/src/update-config.json` 必须一致，固定 Ed25519 公钥已写入。私钥在忽略提交的 `.release-signing/release-private.pem`，必须安全备份、保留，不能进入下载包、日志或源码提交；不要换一把新密钥继续给旧 App 发更新。
2. Android 使用既有 `.android-signing/xiayuluo-private.p12` 和密码，包名仍是 `com.xiayuluo.assistant`。禁止替换为公共 debug key，否则不能覆盖升级。
3. 源码和 `.github/workflows/release.yml` 保存到对应 GitHub 仓库；不提交 `.env`、`.assistant`、`.venv`、私钥和数据库。源码推送只会触发独立 Windows 构建，不会自动配置仓库 Secrets、推送版本标签或发布 Release；正式更新发布仍需确认访问方式并设置签名 Secrets。
4. 在仓库 Actions Secrets 安全设置：
   - `XIAYULUO_RELEASE_PRIVATE_KEY`：既有 Ed25519 私钥的 PEM。
   - `XIAYULUO_ANDROID_KEYSTORE_BASE64`：既有 Android PKCS12 的 Base64。
   - `XIAYULUO_ANDROID_STORE_PASSWORD`：既有 Android 密码。
   不在聊天里提供这些值，也不写入工作流文件。
5. 每次发布同步递增 `assistant/__init__.py`、`pyproject.toml`、`mobile/package.json`、`mobile/app.json`，更新 npm 锁文件和 `RELEASE_NOTES.md`，Android versionCode 递增。推送对应 `vX.Y.Z` 标签，或在该标签运行发布工作流。

只有 Windows 和 Android 都构建/验证成功、私钥匹配固定公钥后，工作流才创建包含两个安装包和清单的草稿 Release，上传完整再公开正式版本。普通代码 push 不发布。首次 Windows 构建、真实双端覆盖升级、GitHub Actions 和实际远程更新源均需验收，不能以模拟测试替代。

## 安全与恢复边界

- 清单格式 `{payload: "原始 JSON 文本", signature: "Ed25519 十六进制签名"}`：先验证原始 UTF-8 文本签名再解析内容。限制应用标识、仓库/标签、大小及 SHA-256，拒绝降级；清单最大 32 KiB，安装包最大 512 MiB。
- Windows 保留 TLS 校验并逐跳限制 GitHub 官方资产重定向。下载完成前不修改安装目录；拒绝目录穿越、符号链接、重复路径、Windows 设备名称、用户数据和解压超限。同盘暂存，独立 helper 等旧进程退出再整体替换，替换或启动进程失败恢复原目录。
- Windows 保留 `.Xiayuluo.backup-*` 旧程序目录供恢复，确认新版可用后可手动删除。它不是数据库备份，不保证自动发现“新 App 启动数秒后崩溃”。本次 helper 结果在系统临时 `xiayuluo-update-*` 下 `job.result.json`，不要在 helper 运行时删除。
- 安装目录含 `.env`、`.assistant`、`.venv`、`.git` 或实际配置/数据/Codex 工作区路径时禁用或拒绝替换。无管理员提权，目录不可写、文件占用、空间不足时保留当前版并报错。
- 安卓只在缓存下载，核对大小及原生 SHA-256 后交系统；失败缓存删除，下次下载替换旧 APK 缓存。系统再检查 APK 签名与版本。返回 App 不等于安装成功，不绕过系统权限或签名。
- 本版没有数据库 schema 变更，不迁移或删除记忆。后续 schema 变更必须另做事务迁移、备份与恢复验收，不能用程序目录回退来回退数据库。

## 验证状态

自动化覆盖签名/篡改、版本比较、URL、大小/哈希、重定向、解压路径、符号链接/重名、目录保留、启动失败回滚、本地鉴权/确认、更新期间禁止新任务，以及记忆不变。手机协议签名与元数据测试、TypeScript 检查通过。尚未 Windows 与安卓真机覆盖升级验收，不能保证所有系统/网络状态下更新必定成功。

0.1.3 完整 Python 测试 129 项通过、2 项 Windows 专属测试跳过，手机协议/下载链接 7 项测试及 TypeScript 检查通过。浏览器验收覆盖：私有清单检查失败时按钮仍可见、打开固定 GitHub 页面、不泄露本地 token/来源、不误报安装成功。Android 构建与核验记录见 ANDROID_INSTALL.md。

Windows Actions 已实际触发，但此前运行在测试步骤失败，尚无已验证的 Windows EXE 产物；尚未取得完整失败日志，不能推断原因或声称已修复。新增按钮不会生成缺失的安装包。Expo 上游开发工具链审计仍有 22 项告警（15 高、7 中），见 MOBILE.md，未用强制降级来掩盖风险。
