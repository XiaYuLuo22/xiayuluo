# 夏鱼洛 Windows 桌面 App

夏鱼洛以独立桌面窗口运行，不需要手动打开浏览器、输入端口或启动服务。默认是面向日常使用的聊天 App，提供会话搜索、流式回复、复制、四级记忆、Codex 和聊天内鼠标控制；调试工作台在「设置 → 开发者模式」按需开启。

界面由 pywebview 和系统 WebView2 呈现，仍复用现有 HTML/CSS/JS 组件，**不是将所有控件重写为 Win32 原生控件**。应用拥有自己的进程、窗口、配置和本地服务。它不使用远程网页，也不依赖 CDN。

## 0.1.11：回复朗读与直接更新按钮

普通新回复完成后默认使用系统中文声音朗读，输入栏「朗读：开/关」和设置开关独立于麦克风，播放时可「停止朗读」。历史、失败/未完成和待确认内容不自动读，代码/链接跳过，长文限长提示，全文仍保留。缺系统中文声音时仅提示不可用，不影响文字聊天；不回退网络TTS、不要求先加载离线识别模型。系统声音后续可替换，当前先用本地中文音色，详见[VOICE.md](VOICE.md)。

「下载更新」改为应用内检查、下载、校验、重启，不跳GitHub；目前仍私有且无签名Release，尚不能从线上下载新版本，会明确提示发布源未就绪。首次仍需获取新安装器并安装一次，本地签名更新包导入继续保留。正式发布源由用户另行配置/发布，详见[UPDATES.md](UPDATES.md)。

## 0.1.10：单实例与聊天鼠标开关

同一 Windows 登录会话只能打开一个夏鱼洛 GUI；再次点击桌面/开始菜单/其他副本时通知已有窗口恢复并显示，第二个启动程序直接退出，不再启动第二套本地服务。正常退出或进程结束自动释放内核锁，不依赖残留 PID 文件、不结束其他程序。开发用 `--serve` 不属于 GUI 实例，勿同时占用手机同步端口。云端单元测试不代替实际 Windows 前台聚焦验收。

不再有独立「屏幕助手」页面。聊天输入栏开启「鼠标控制」后，发送 `请点击“设置”按钮` 等明确文字指令，临时取一张主屏幕截图交给所选 Codex/视觉 API 定位，再由本机校验并执行一次。普通聊天不截图，服务重启后开关关闭；关掉开关取消规划并作废旧截图，已发生操作不能撤回。支持点击/右键/双击/移动/有限滚动，不支持键盘、手机/语音授权和自主多步操作。详见 [CODEX_CHAT.md](CODEX_CHAT.md)。Windows首次空白配置已启用驱动；若旧 `.env` 显式 `DESKTOP_ENABLED=false`，仍尊重该配置，需改为 true 后重启。

## 日常使用：安装器与桌面图标

源码保持私有。登录有权限的 GitHub 账号，打开 Windows Actions 的**成功运行**，下载 `Xiayuluo-Windows` artifact，完整解压后双击 `Xiayuluo-Setup.exe`，按图形向导安装。存在 E 盘时默认 `E:\Xiayuluo`，否则使用用户目录 `Programs\Xiayuluo`，均可在向导中选择其他可写位置；无需管理员权限、Python 或 CMD。以后双击桌面「夏鱼洛」或开始菜单图标即可，直接目标是 `Xiayuluo.exe`。未做 Windows 代码签名，系统可能提示 SmartScreen；WebView2 与 Codex 首次登录仍需系统/官方流程。

原生发行版启动及每小时可见空闲时检查正式签名版本，发现后显示顶栏「更新」和设置里的「下载并重启更新」，不自动安装；网络、私有权限或未发布时如实显示失败，不影响当前版。用户将自行公开仓库，代理不修改可见性；源码公开后仍需正式发布签名清单与安装包，才可在线下载。也可从私人渠道下载 `.xyupdate`，进入 App「设置 → 版本与更新 → 选择更新包并重启」，验证签名/版本/哈希后自动重启替换，保留外部配置、记忆与卸载器。取消、错误或旧版本不安装。普通安装器也可在关闭旧版后用于覆盖安装，卸载不主动删除 `%LOCALAPPDATA%\Xiayuluo` 的用户数据。

首次从源码迁移：先关闭源码版，备份原 `.env` 和 `.assistant`；在新 App 菜单「打开配置目录」定位 `%LOCALAPPDATA%\Xiayuluo`，关闭 App 后用文件管理器迁移配置和记忆（不要覆盖已有数据，先备份）。新安装器不会擅自搜索或读取旧源码目录，也不将旧数据自动打包。Android 0.1.7 可继续配对，无需为电脑安装方式重装。

### 开发者源码入口（非日常发行版）

0.1.4 语音测试功能需要另外安装 `.[app,desktop,voice]`、准备官方中文离线模型并具备系统中文 SAPI 声音；普通 `start.cmd` 的文字聊天不强制下载模型。模型已在云端获取并校验，未完成 Windows 麦克风验收；Windows 安装包仍须在 Windows 构建，不能把源码通过当成 EXE 已验收。设置页会明确显示不可用原因，见 [VOICE.md](VOICE.md)。

日常问答可以使用官方 Codex CLI 的 ChatGPT 登录，不需要模型 API 密钥或项目目录；需要支持安全开关的新版 CLI，默认发送即调用，执行方式可在设置修改。0.1.8 在界面启动/选择 Codex 时自动识别现有 CLI 登录，设置可重新检测；无需先打开 ChatGPT/Codex 窗口，仅装桌面 App 不代表 CLI 已安装或登录。检测只验证本地登录方式，网络/权限/额度以实际回复为准，完整步骤见 [CODEX_CHAT.md](CODEX_CHAT.md)。编程模式仍需项目目录。

0.1.8 本次提供电脑源码 ZIP，不是 EXE。更新前关闭夏鱼洛并备份原目录；只更新源码，保留自己的 `.env`、`.assistant`、语音模型与虚拟环境，再运行 `start.cmd`。已配对的安卓 0.1.7 协议兼容，无需重装。

需要 Windows 10/11、Python 3.11+ 和 [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)。许多 Windows 电脑已有此运行库。需要 Codex 时另外安装 Node.js 22+ 和 Codex CLI。

推荐：安装 Python 后，完整解压并双击 `start.cmd`，不用手动输入安装命令。脚本优先使用已有 `.venv`，其次尝试 Python Launcher 的 3.12、默认 Python 3 或 PATH 上的 Python；首次创建专用环境并安装锁定约束下的 `.[app,desktop]`。后续实际检查导入和依赖一致性；配置未变且检查通过时跳过下载。缺少依赖会重新安装，安装失败不启动 App，错误窗口保留。它不会覆盖 `.env`、删除记忆或自动安装 Codex。请勿同时打开多个安装窗口。

旧源码包的用户可以把启动补丁中的 **`start.cmd` 和 `tools/bootstrap_windows.py`** 解压到原文件夹，仅覆盖同名启动文件；不要删除或重新覆盖自己的 `.env` 与 `.assistant`。Python 未安装时仍需先安装一次，建议 64 位 Python 3.12。WebView2 系统运行库也不由此脚本自动安装。

手动安装方式仍可使用，在项目目录运行：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -c requirements.lock -e ".[app,desktop]"
.\.venv\Scripts\python.exe -m assistant
```

`start.cmd` 会保留一个启动窗口用于安装和错误诊断，App 仍在独立桌面窗口中运行；正常关闭 App 后启动窗口退出。安装完成后也可以使用 `xiayuluo-app` 或直接运行 `.venv\Scripts\pythonw.exe -m assistant` 无控制台启动，但这种方式不会自动修复依赖。

源码模式沿用项目目录的 `.env` 和 `.assistant` 记忆数据。首次启动仅在 `.env` 不存在时从空白模板生成配置，不会覆盖已有配置。Windows 首次生成的模板会启用桌面驱动；聊天栏鼠标开关仍默认关闭，开启并明确提出指令后按当前 auto/manual 策略执行。

## 生成可分发的 .exe

必须在 **Windows** 上构建。运行：

```powershell
.\build-app.cmd
```

也可分步运行：

```powershell
.\.venv\Scripts\python.exe -m pip install -c requirements.lock -e ".[app,desktop,voice,build]"
.\.venv\Scripts\python.exe tools\prepare_voice_model.py
.\.venv\Scripts\python.exe tools\build_desktop.py
```

产物为 `dist/desktop/Xiayuluo-Windows.zip`。解压**整个文件夹**，双击 `Xiayuluo/Xiayuluo.exe`；不能只复制一个 exe，因为 `_internal` 中保存必要的 Python、界面和桌面框架依赖。此发行版不需要用户安装 Python，但仍需要 WebView2 Runtime。Codex CLI 与登录不会一起打包。

构建脚本保留已存在的发行包，重复构建请使用新的目录：

```powershell
.\build-app.cmd --output-dir dist\desktop-next
```

构建中间文件使用临时目录，完成或失败后自动清理；用户的 `.env`、API 密钥、记忆数据库和 Codex 凭据不会打包。发行包附带依赖许可证与第三方说明。普通脚本生成文件夹式发行包；`--installer` 另外生成 Inno Setup 安装向导，没有 Windows 代码签名或开机自启。0.1.1 起包含更新 helper，可在设置中确认下载、重启升级；仍需正式发布更新源和 Windows 验收，见 [UPDATES.md](UPDATES.md)。更新清单签名不是 Windows Authenticode，系统仍可能提示 SmartScreen。

源码推送到 `main` 后会自动运行 **Build Windows desktop app**，也可在 Actions 手动运行；成功后下载 `Xiayuluo-Windows` artifact。工作流只读仓库，不需要模型密钥或发布私钥，不创建公开 Release。解开 artifact 后，再解压里面的 `Xiayuluo-Windows.zip` 到 E 盘，双击 `Xiayuluo/Xiayuluo.exe`。以 Actions 的实际状态为准，源码推送成功不等于 Windows 构建或本机启动已经验收。

## 发行版配置与记忆

发行版使用 `%LOCALAPPDATA%\Xiayuluo`，例如 `C:\Users\you\AppData\Local\Xiayuluo`：

- `.env`：本机模型密钥、Codex 项目目录等配置。
- `.assistant\memory.sqlite3`：四级记忆和历史对话。
- `.assistant\desktop.log`：桌面启动/运行错误日志，最多保留两个轮转备份。
- `.assistant\speech.json`：本机回复朗读开关和系统音色编号，不含声音样本或聊天文本。

原生菜单「夏鱼洛 → 打开配置目录」可定位配置，「打开日志目录」可定位日志。「重新加载界面」只刷新界面；修改 `.env` 后要**退出并重新启动 App**。

使用模型 API 时的配置示例（仅用 Codex 日常问答不需要这三个 OPENAI 字段）：

```dotenv
OPENAI_API_KEY=你的模型密钥
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4.1-mini
CODEX_WORKSPACE=C:/Users/you/projects/my-project
CODEX_SANDBOX=read-only
DESKTOP_ENABLED=true
```

从源码版迁移时，先关闭所有夏鱼洛实例，再将原 `.env` 和 `.assistant` 目录复制到上述用户目录；已有数据应先备份。应用不会自动覆盖或搬移旧记忆。

内部服务只绑定本机回环地址，默认使用自动分配的端口。显式设置 `MOBILE_ENABLED=true` 时使用 `ASSISTANT_PORT`（默认 8765），供受控 HTTPS 代理连接；手机同步默认关闭，配置和权限边界见 [MOBILE.md](MOBILE.md)。`--serve` 也使用该固定端口，不要与手机模式的 App 同时运行。关闭窗口会确认退出，停止本 App 的服务和活动任务；已执行的点击、已写入的代码变更不会撤回。

外部文档链接由系统浏览器打开；日志导出使用原生保存对话框。API 仍使用同源检查和本地会话令牌，插件遵循设置中的 auto/manual 策略，没有新增可以直接执行任意 Python 的页面桥接。

## 开发模式与验证范围

在没有图形桌面的云开发机上运行后端：

```bash
.venv/bin/python -m assistant --serve
.venv/bin/python -m pytest -q
```

Windows 上可用 `python -m assistant --debug` 打开内嵌界面的开发者工具。Linux 桌面需要 pywebview 支持的 GTK 或 Qt 渲染器；当前 Linux 云机没有图形桌面，不会为测试安装完整桌面系统。

当前自动化测试实际启动 HTTP 服务，验证随机端口、就绪请求、配置保存、窗口关闭事件、渲染器失败清理和活动 Codex 子进程停止；使用 pywebview 的真实窗口对象检查 API 契约。**这不等于 Windows WebView2 窗口、最终 .exe 和真实鼠标已经验收**，这些应在 Windows 本机完成。云机不能交叉生成 Windows .exe。
