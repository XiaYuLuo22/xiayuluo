# 夏鱼洛 Windows 桌面 App

夏鱼洛以独立桌面窗口运行，不需要手动打开浏览器、输入端口或启动服务。默认是面向日常使用的聊天 App，提供会话搜索、流式回复、复制、四级记忆、Codex 和屏幕协助；调试工作台在「设置 → 开发者模式」按需开启。

界面由 pywebview 和系统 WebView2 呈现，仍复用现有 HTML/CSS/JS 组件，**不是将所有控件重写为 Win32 原生控件**。应用拥有自己的进程、窗口、配置和本地服务。它不使用远程网页，也不依赖 CDN。

## 从源码直接启动

0.1.4 语音测试功能需要另外安装 `.[app,desktop,voice]`、准备官方中文离线模型并具备系统中文 SAPI 声音；普通 `start.cmd` 的文字聊天不强制下载模型。模型已在云端获取并校验，未完成 Windows 麦克风验收；Windows 安装包仍须在 Windows 构建，不能把源码通过当成 EXE 已验收。设置页会明确显示不可用原因，见 [VOICE.md](VOICE.md)。

日常问答可以使用官方 Codex CLI 的 ChatGPT 登录，不需要模型 API 密钥或项目目录；需要支持安全开关的新版 CLI，发送前逐次确认。仅装 Codex 桌面 App 不代表 CLI 已可用，完整步骤见 [CODEX_CHAT.md](CODEX_CHAT.md)。编程模式仍需项目目录。

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

源码模式沿用项目目录的 `.env` 和 `.assistant` 记忆数据。首次启动仅在 `.env` 不存在时从空白模板生成配置，不会覆盖已有配置。Windows 首次生成的模板会启用桌面驱动；截图和点击仍需要用户主动操作及确认。

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

构建中间文件使用临时目录，完成或失败后自动清理；用户的 `.env`、API 密钥、记忆数据库和 Codex 凭据不会打包。发行包附带依赖许可证与第三方说明。当前是文件夹式发行包，没有安装向导、Windows 代码签名或开机自启。0.1.1 起包含更新 helper，可在设置中确认下载、重启升级；仍需正式发布更新源和 Windows 验收，见 [UPDATES.md](UPDATES.md)。更新清单签名不是 Windows Authenticode，系统仍可能提示 SmartScreen。

源码推送到 `main` 后会自动运行 **Build Windows desktop app**，也可在 Actions 手动运行；成功后下载 `Xiayuluo-Windows` artifact。工作流只读仓库，不需要模型密钥或发布私钥，不创建公开 Release。解开 artifact 后，再解压里面的 `Xiayuluo-Windows.zip` 到 E 盘，双击 `Xiayuluo/Xiayuluo.exe`。以 Actions 的实际状态为准，源码推送成功不等于 Windows 构建或本机启动已经验收。

## 发行版配置与记忆

发行版使用 `%LOCALAPPDATA%\Xiayuluo`，例如 `C:\Users\you\AppData\Local\Xiayuluo`：

- `.env`：本机模型密钥、Codex 项目目录等配置。
- `.assistant\memory.sqlite3`：四级记忆和历史对话。
- `.assistant\desktop.log`：桌面启动/运行错误日志，最多保留两个轮转备份。

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

外部文档链接由系统浏览器打开；日志导出使用原生保存对话框。API 仍使用同源检查和本地会话令牌，插件仍走用户确认流程，没有新增可以直接执行任意 Python 的页面桥接。

## 开发模式与验证范围

在没有图形桌面的云开发机上运行后端：

```bash
.venv/bin/python -m assistant --serve
.venv/bin/python -m pytest -q
```

Windows 上可用 `python -m assistant --debug` 打开内嵌界面的开发者工具。Linux 桌面需要 pywebview 支持的 GTK 或 Qt 渲染器；当前 Linux 云机没有图形桌面，不会为测试安装完整桌面系统。

当前自动化测试实际启动 HTTP 服务，验证随机端口、就绪请求、配置保存、窗口关闭事件、渲染器失败清理和活动 Codex 子进程停止；使用 pywebview 的真实窗口对象检查 API 契约。**这不等于 Windows WebView2 窗口、最终 .exe 和真实鼠标已经验收**，这些应在 Windows 本机完成。云机不能交叉生成 Windows .exe。
