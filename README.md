# 夏鱼洛 · 你的日常桌面助手

Windows 优先的可扩展桌面 App：独立窗口、日常文字对话、流式响应、本机 Codex 调用、聊天内开关控制鼠标，以及按保留时长分层的四级记忆。默认入口会自动管理内部服务，不需要手动打开浏览器。桌面运行、.exe 打包和用户数据路径见 [DESKTOP.md](DESKTOP.md)。

这是私人测试版。已有语音测试功能，键盘输入、多屏幕控制和无人值守自主操作尚未实现；真实设备仍需验收。

0.1.2 日常对话可使用官方 Codex CLI 的 ChatGPT 登录，不再强制填写模型 API 密钥。设置中选择「Codex · ChatGPT 登录」，按确认流程发送近期上下文和相关记忆；默认无 API 密钥时也会提出该问答请求。它不操控 Codex 桌面窗口、不提取令牌、不绕过会员额度，也不保证逐字输出，详见 [CODEX_CHAT.md](CODEX_CHAT.md)。

0.1.4 加入双端语音测试功能：本地识别“夏鱼洛”、Windows 最小化监听、Android 显式开启的麦克风前台服务及常驻通知。用户一次授权仅限本次语音的 Codex 只读问答，编程/鼠标/永久记忆仍需确认。官方离线中文模型已下载并校验，安卓 release 私人测试包已构建；未真机验收，Windows EXE 仍需构建/验证，不能保证任意后台状态唤醒。详见 [VOICE.md](VOICE.md)。

0.1.3 新增始终可见的「下载更新」按钮：电脑「设置 → 版本与更新」、安卓「设备 → 版本与更新」。仓库暂时保持私有，按钮可打开 GitHub 页面，登录后手动下载；需要已有成功构建或发布的安装包。检查到签名新版时仍沿用安全安装流程。打开网页不代表更新已安装，旧 App 需先覆盖安装新版才能看到按钮。详见 [UPDATES.md](UPDATES.md)。

新增手机 App：`mobile/` 使用 Expo / React Native，Android、iOS 共用聊天、记忆、任务、设备界面。已构建并校验 Android ARM64 私人测试 APK，安装说明见 [ANDROID_INSTALL.md](ANDROID_INSTALL.md)；iOS 尚无 IPA，尚未真机验收，也没有已部署的公网同步服务。可选的电脑主机同步通过 HTTPS 与一次性配对共享会话和四级记忆，手机仅能提出电脑任务，批准和执行留在电脑端。同步默认关闭；设计、连接步骤和安全/构建限制见 [MOBILE.md](MOBILE.md)。

对话支持 Markdown、代码块、列表和表格排版。前端使用官方 [Marked](https://github.com/markedjs/marked) 和 [DOMPurify](https://github.com/cure53/DOMPurify) 的锁定发布版；运行文件随应用提供，聊天时不访问 CDN。用户输入保持纯文本，助手内容经过 HTML 过滤；回复中的远程图片、表单、脚本和事件处理器不被加载。链接只支持明确的 HTTP(S) 地址，点击后在新标签页打开。

## 日常聊天 App

0.1.11 新回复完成后默认系统本地中文朗读，文字和声音兼有，输入栏/设置独立开关与停止入口，不要求开启麦克风；先用系统音色，后续可换音源。不会自动读历史、失败/未完成或待确认的内容，代码/链接省略，长回复限长且提示，详见[VOICE.md](VOICE.md)。两端「下载更新」改为App内检查、下载校验和安装，不再跳GitHub；仓库仍私有且无正式签名发布，实际在线直更尚未就绪，不能靠按钮绕过发布/访问权限，详见[UPDATES.md](UPDATES.md)。

0.1.10 移除独立屏幕助手，在聊天输入栏增加「鼠标控制：关/开」。开启后直接输入点击、双击、移动或滚动要求，临时截图交给所选 Codex ChatGPT 登录/视觉 API 定位并执行一次；普通对话不读屏，服务启动默认关闭，关掉后旧截图和规划立即失效，已发生的动作不能撤回。Windows 同一登录会话只打开一个 GUI，重复启动唤起已有窗口并退出，不再启动重复服务。现有安卓 0.1.7 协议兼容，无新 APK；手机/语音不能继承鼠标授权。

0.1.9 Windows 构建增加 `Xiayuluo-Setup.exe` 图形安装器，建立桌面和开始菜单图标，直接打开 App，无需 CMD/Python；E 盘存在时默认安装到 E:\Xiayuluo，可改位置。原生发行版启动后自动检查正式签名版本，发现新版可一键下载、校验、重启，也支持「选择更新包并重启」导入私人 `.xyupdate`，保留外部配置和记忆。用户计划自行公开仓库，目前仍私有且未发布签名更新源；不能把源码公开当成更新已上线。首次要安装一次发行版，旧源码不能凭空变成 exe。见 [DESKTOP.md](DESKTOP.md) 与 [UPDATES.md](UPDATES.md)。

0.1.8 在电脑界面启动时自动识别官方 Codex CLI 的现有 ChatGPT 登录，设置可重新检测，无需每次配置或预先打开 ChatGPT/Codex 桌面窗口。首次仍需安装 CLI 并 `codex login`；不读取 GUI 登录、不发测试问题、不改变显式 API 来源，网络与会员额度以实际回复为准。本次提供电脑源码更新，已配对的 0.1.7 安卓 App 协议兼容无需重装，未构建 0.1.8 APK 或 Windows EXE。详见 [CODEX_CHAT.md](CODEX_CHAT.md)。

0.1.7 按用户明确授权，原生 App 打开后自动尝试语音待命：Android 不必进入设备页，Windows 在原生窗口显示后尝试，首次系统授权仍需本人允许。停止/拒绝/失败后本次不循环重试，语音状态全局可见；浏览器和 `--serve` 不自启监听。仍需配对电脑在线、语音依赖/模型/系统中文朗读与 Codex 登录，未真机验收，不保证始终唤醒。完整条件见 [VOICE.md](VOICE.md)。
0.1.6 已按用户授权改为 **默认同意**：普通 Codex 问答发送即调用，编程、有效截图点击和用户要求的记忆保存按现有权限直接执行，不再逐次弹应用确认。电脑「设置 → 执行方式」可改回逐次确认并保存；默认环境项为 `APPROVAL_MODE=auto`，已保存设置优先。无需自动开麦、关闭沙箱或绕过系统权限；仍可停止，已执行的操作不能撤回。手机遵循电脑执行方式，仍需安全配对，详见 [CODEX_CHAT.md](CODEX_CHAT.md)。

0.1.5 的手机「任务 / 设备」和电脑「设备与同步」显示共享项目编号及读写权限。手机编程指令绑定此工作区，电脑逐次确认后在指定目录执行，结果返回同一会话；变更目录或沙箱须重新提交。电脑保持开机、不休眠、服务运行并完成 HTTPS 配对才可使用，详见 [MOBILE.md](MOBILE.md#让手机发指令电脑执行)。这不是连接手机 ChatGPT App 的内部工作区。

默认界面采用简洁的聊天布局：左侧会话历史与搜索、中间对话、底部输入框。首页可直接开始日常聊天、Codex 编程或记忆管理，鼠标操作使用聊天栏独立开关；回复支持流式显示与复制，历史会话可以切换和删除。未连接模型时，首页显示设置入口，而不会声称模型已可用。

它参考常见聊天助手的交互习惯，保留夏鱼洛自己的视觉设计；不是 ChatGPT 或豆包的客户端，不读取它们的 App 登录。可使用已登录的官方 Codex CLI，或在本机 `.env` 中配置模型 API；已有语音测试和 Windows 安装器，云账号同步尚未实现。手机开发原型与当前构建状态见 MOBILE.md。

## 可选开发者模式

在「设置」开启「开发者模式」，切换到深色工作台，显示当前运行状态、首段耗时、总耗时、实际 SSE 事件数和输出字符数；右侧 Inspector 提供运行配置、四级记忆计数、最近一次召回详情和最近事件。窄屏时可以用顶栏按钮打开或关闭检查面板。默认关闭，日常界面不展示日志、参数 Schema 或技术状态栏。关闭开发者模式会同时停止文本日志记录并移除已有日志中的文本。偏好仅保存在该窗口的 WebView 存储中，桌面 App 重新启动后不保证保留。

「事件日志」支持按 HTTP、SSE、工具操作和错误筛选、展开 JSON、清空和导出。日志仅保存在当前窗口内存中，最多保留 300 条；刷新后清空，不写入记忆数据库。默认只记录元数据，勾选「记录后续事件的文本内容」后才保留后续对话/工具文本；取消勾选会移除已有日志中的文本。密钥、认证头和截图始终被过滤。

指标是**浏览器实际观测值**：首段耗时从发起流式请求计算到接收第一段非空文本，包含网络与本地处理；SSE 事件数不是模型 token 数。日志不包含模型隐藏推理或 Codex 内部思考。开启开发者模式后，工具参数 Schema 在「工具箱」中查看，平台、Python、接口地址和沙箱等元数据在「设置」中查看；配置存在不代表已经通过真实连接验证。

## Windows 启动

安装 64 位 Python 3.12（或 Python 3.11+）后，完整解压，直接双击 **`start.cmd`**。首次自动创建 `.venv` 并联网安装 App、聊天服务和屏幕工具依赖；后续检查通过会跳过重复安装。缺失 `uvicorn` 等依赖会尝试修复，失败时窗口停留并展示原因。无需管理员权限，不覆盖已有 `.env` 和记忆；安装时请勿同时打开多个启动窗口。

Python 或 WebView2 缺失时会给出官方下载地址，不会自动安装系统软件。模型连接和 Codex 登录仍需要你配置；Codex 不在 Python 依赖中自动安装。需要它时另装 Node.js 22+ 和 Codex CLI。

如需手动安装，可在仓库目录运行 PowerShell：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -c requirements.lock -e ".[app,desktop]"
```

如果使用模型 API，再编辑 `.env` 填写下列连接配置；仅使用 Codex 登录问答无需填写 OPENAI_API_KEY，按 CODEX_CHAT.md 登录并选择 Codex 即可。密钥只存在本机，切勿提交到 Git：

```dotenv
OPENAI_API_KEY=你的模型密钥
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4.1-mini
DESKTOP_ENABLED=true
CODEX_WORKSPACE=C:/Users/you/projects/my-project
CODEX_SANDBOX=read-only
```

```powershell
.\.venv\Scripts\python.exe -m assistant
```

运行后会打开「夏鱼洛」桌面窗口；首次启动会在 `.env` 不存在时生成空白配置。修改 `.env` 后需要退出并重新启动 App。一键启动会保留启动窗口供诊断，关闭 App 后正常退出；关闭窗口会停止本 App 的内部服务和活动任务。要生成免安装的 Windows .exe，请运行 `build-app.cmd`；完整说明见 [DESKTOP.md](DESKTOP.md)。

模型需支持 OpenAI 兼容的 `/chat/completions` 流式接口；工具调用需要 function calling，理解截图需要视觉能力。本地 OpenAI 兼容服务也可使用，将地址设为 `http://127.0.0.1:端口/v1`，并按该服务要求配置密钥。模型未配置时，仍可管理记忆和使用已配置的本机插件。

云环境如保留了 `OPENAI_` 变量名，可通过环境设置安全绑定 `ASSISTANT_MODEL_KEY`；应用优先使用此变量。它与本机的 `OPENAI_API_KEY` 用途相同。云网络需允许模型服务域名；使用 ChatGPT 登录的 Codex 时还需允许 `chatgpt.com`。云环境的网络和 CLI 状态不代表你的 Windows 本机状态。

## Codex

```powershell
npm install -g @openai/codex
codex login
```

设置 `CODEX_WORKSPACE` 为**已有项目目录**。选择聊天输入框的「Codex 编程」，发送任务并确认目录、沙箱与任务内容后执行。也可在日常对话中要求助手调用 Codex。

默认 `read-only`。如需修改该项目，将 `CODEX_SANDBOX` 改为 `workspace-write` 并重启；每个任务仍需确认。应用不会使用禁用沙箱或绕过审批的选项。Codex 自身的登录、网络、沙箱支持和执行权限必须正常；遇到问题先在目标项目目录直接运行 `codex`。Windows 沙箱如被 CLI 提示未初始化，先按 CLI 的引导完成初始化。公司代理或特殊配置应通过 CLI 的支持方式配置。

Codex 的任务状态实时显示；CLI 的完整消息在相应 JSON 事件到达时显示，**不保证 Codex 输出逐字显示**。模型日常对话采用独立的 token 流。停止任务会终止本应用启动的 CLI 进程树；已写入文件的变更不会自动撤销。

## 聊天内鼠标控制

在 Windows App 聊天输入栏开启「鼠标控制」，发送 `请点击“设置”按钮`、`双击桌面文件夹`、`把鼠标移动到搜索框` 或 `向下滚动三格`。无需独立屏幕页面、手动截图或输入坐标；按所选引擎使用 Codex ChatGPT 登录（无需 API 密钥）或显式视觉 API。默认 auto 直接执行，manual 仍待批准，一轮最多一个鼠标动作，不能输入文字或自主连续操作。

服务启动时开关默认关闭；开启不等于持续录屏，只有明确的本轮鼠标指令才取一张主屏幕新截图并发给所选引擎，截图不写入聊天/记忆。结束清理自有临时图片；手动待确认仅保留短时本机内存帧。关掉开关取消规划、清空旧截图，重新开启不复活旧请求。手机和语音只读租约不能开启此权限。

截图有效期 2 分钟。操作前检查主屏幕大小和目标周围 41×41 像素，目标改变、越界、尺寸不符或截图过期会拒绝执行，不猜测坐标。将鼠标移到主屏幕左上角，可触发 PyAutoGUI 的 fail-safe。已发生操作无法撤回。

首版只控制**主屏幕**。Windows 使用 DPI awareness 校正坐标，若实际截图与鼠标尺寸仍不一致，会拒绝点击。管理员/UAC 安全桌面和某些高权限应用可能无法控制。当前云开发机没有你的 Windows 桌面；真实截图和点击必须在你的本机验收。

Windows 安装版首次空白配置已启用桌面驱动；源码需要安装 `desktop` 依赖并设置 `DESKTOP_ENABLED=true`。保留旧 `.env` 的显式禁用，不擅自覆盖。动态视频、动画按钮或鼠标悬停导致目标变化时，请重新发出明确指令。Codex 需新版官方 CLI 支持图片输入/结构化输出；不支持时明确失败、不删安全限制重试，详见 [CODEX_CHAT.md](CODEX_CHAT.md)。

## 四级记忆

这四级按保留时长和强化程度划分；事实、偏好、经历、方法是**内容类型**，不是层级。

| 层级 | 默认规则 | 来源与晋升 |
| --- | --- | --- |
| 临时性 | 内存中最近 12 条消息，最多 18,000 字符；闲置 2 小时或重启后清空 | 当前对话上下文，不自动变成永久记忆 |
| 一级 | 7 天未使用后退出检索 | 用户消息自动形成原始对话片段；累计检索 3 次晋升二级 |
| 二级 | 每次检索后续期 30 天 | 一级强化或用户明确保存；保留原始内容类型，不把提问推断为事实 |
| 永久 | 不自动过期 | 仅由用户保存/确认晋升；可手动降级或删除 |

检索采用本机中文二字词与英文词匹配，综合相关性、近期使用和重复使用次数，最多取 6 条。经过确认的二级/永久事实会作为偏好背景参与对话；经历和方法需相关匹配。**这是可解释的基础实现，尚未使用向量嵌入、模型总结或睡眠整理。**

临时记忆、全部历史聊天与可检索记忆是不同用途：历史消息保存在本机供用户回看；过期的一级/二级记忆不会再被召回，但不会静默删除历史。记忆页支持添加、永久化、降级和删除；删除会话会删除它自动生成的记忆，手动另存的内容会保留。清空操作删除所有记忆与聊天记录，并压缩数据库；外部备份不在删除范围内。

数据位于 `.assistant/memory.sqlite3`，可用 `ASSISTANT_DATA_DIR` 修改。关闭服务后再备份整个数据目录。数据是普通本机 SQLite 文件，**未加密**；可以通过 Windows 磁盘加密保护，不建议记录敏感凭据。每次对话会把临时上下文和召回的相关记忆发给配置的模型服务，Codex 任务则使用其自身配置的服务。

## 新增能力

插件接口在 `assistant/plugins.py`；注册点是 `assistant/tools.py` 的 `build_registry()`。插件需提供名称、说明、Pydantic 参数模型、可用性检查和异步 `execute()` 方法。例如：

```python
from pydantic import BaseModel, ConfigDict

class ExampleArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic: str

class ExamplePlugin:
    name = "example_tool"
    title = "示例能力"
    description = "按主题执行一个任务；需用户确认。"
    arguments = ExampleArgs

    def available(self):
        return True, ""

    async def execute(self, arguments):
        return {"message": f"处理完成：{arguments.topic}"}

# 在 build_registry 中加入：registry.register(ExamplePlugin())
```

界面自动从插件 catalog 渲染新增能力和 JSON 参数表单；模型自动获得可用工具的 schema。所有新增插件也走「提出请求 → 用户确认 → 执行」流程。插件是本机可信 Python 代码，不应安装不可信来源。新增依赖、权限和配置需要在插件中说明。语音后续可增加浏览器音频流或 Realtime API 适配层，复用记忆和插件服务。

## 开发与测试

```powershell
.\.venv\Scripts\python.exe -m pip install -c requirements.lock -e ".[test]"
.\.venv\Scripts\python.exe -m pytest -q
```

Linux 云开发机可以不安装桌面依赖：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -c requirements.lock -e '.[test]'
.venv/bin/python -m pytest -q
.venv/bin/python -m assistant --serve
```

浏览器验收测试是独立可选依赖；安装后使用本机 Chromium 或 Playwright 的浏览器：

```bash
.venv/bin/python -m pip install -c requirements.lock -e '.[test,browser]'
# 如系统没有 Chromium：
.venv/bin/python -m playwright install chromium
.venv/bin/python -m pytest -q
```

未安装 `browser` 时，浏览器测试会明确标记为跳过；核心 API、记忆和工具测试仍执行。可用 `ASSISTANT_BROWSER` 指定 Chromium 可执行文件。

自动化测试使用受控模型流与桌面替身，不需要真实 API 密钥，也不会点击真实桌面。应用只接受本机 Host、同源请求和本轮服务生成的会话令牌；配置页面只显示是否配置，不输出密钥。请保持回环监听，不将控制接口直接暴露到公网。

## 开源依赖与临时文件

依赖来源、版本和许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。复用有明确用途的组件，插件也通过同样的来源、许可和参数校验流程接入。

校验已随应用保存的前端运行文件，不需要网络：

```powershell
.\.venv\Scripts\python.exe tools/vendor_frontend.py --check
```

缺失时可用 `tools/vendor_frontend.py` 从官方 npm HTTPS 发布包恢复锁定版本。恢复时校验归档 SHA-512 和文件 SHA-256，仅保留运行文件和许可证；不会执行 npm 安装脚本，也不会留下解包目录。运行应用不需要执行此恢复步骤。

开发结束后可清理 `build/`、`.pytest_cache/`、项目源码中的 `__pycache__/`、已使用的安装下载缓存和临时发布包，它们可重新生成。`.venv/` 是当前运行依赖；`.env` 是本机配置；`.assistant/` 是记忆数据；源码、测试和许可证是项目的一部分，应保留。项目的 `.egg-info/` 在 editable 安装期间承载 Python 包元数据，运行中不删除。`tools/` 保存可复用的依赖校验工具，不是一次性下载目录。
