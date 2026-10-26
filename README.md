# 夏鱼洛 · 你的日常桌面助手

Windows 优先的可扩展桌面 App：独立窗口、日常文字对话、流式响应、本机 Codex 调用、聊天内开关控制鼠标，以及按保留时长分层的四级记忆。默认入口会自动管理内部服务，不需要手动打开浏览器。桌面运行、.exe 打包和用户数据路径见 [DESKTOP.md](DESKTOP.md)。

这是个人使用的测试项目，源码仓库已由所有者公开。公开源码不等于已经发布安装包、签名更新清单或私人云同步服务；真实设备仍需验收，不能把云端测试通过当成你的电脑或手机已可用。

## 当前开发状态

当前源码 0.1.15 继续完善 App 内更新：Windows 下载进度和启动健康确认、Android 取消与安装器重试、发布前后二次核验；0.1.14 的活动感知、离线视觉学习、训练数据保留和可自部署同步模块继续保留。模块实现、安装包构建、正式 Release、真机验收是不同状态；双端版本元数据同步不表示已经生成新 APK。

实际签名预检已确认三项 Actions Secrets 尚未配置，见 [在线更新配置与阻塞](UPDATES.md#015-客户端完善与发布阻塞)。本轮没有上传私钥、重建签名或发布不完整版本；这些配置由所有者安全补齐后才能正式上线。

| 能力 | 当前范围 |
| --- | --- |
| 聊天与 Codex | API 可流式回复；官方 Codex CLI 可复用 ChatGPT 登录，默认自动调用已配置工具，不保证逐 token 输出；不连接或提取 GPT/Codex 桌面 App 登录 |
| 手机发对话/任务 | 现有 HTTPS 配对连接在线电脑，聊天、记忆和绑定项目的 Codex 指令由电脑处理；不提供手机鼠标遥控，不读取手机其他 App |
| 独立云同步 | 新增可自部署服务、持久事件/离线队列和电脑端可选文本同步；没有云服务器，尚未部署，手机现成 App 的云接入与云任务执行闭环仍未完成，见 [CLOUD_SYNC.md](CLOUD_SYNC.md) |
| 公开 GitHub 加密备份 | 本人显式选择一致性导出/已验证训练 ZIP，本机加密后显式上传；单个明文文件最多 16 MiB，密钥只本机保存；不是实时同步或完整训练云盘，公开历史不能保证删除，见 [CLOUD_SYNC.md](CLOUD_SYNC.md) |
| 活动感知 | Windows 独立、默认关闭的观察开关；有界周期检测和短期摘要供本机对话参考，概率建议与观察分开；不能理解全部活动、锁屏或安全界面，见 [ACTIVITY.md](ACTIVITY.md) |
| 视觉动作学习 | Neuro 思路的自研像素＋目标离线实验；扩展明确提供的示范图片/任务/动作标签导入、训练与评测，不自动采集、不执行真实鼠标，不宣称通用桌面能力，见 [VISION_LEARNING.md](VISION_LEARNING.md) |
| 更新和训练数据 | App 内检查签名版本、下载校验后安装；训练根与程序分离，更新前校验备份，失败中止。仍需可访问的完整签名 Release；同盘备份不防硬盘损坏，见 [UPDATES.md](UPDATES.md)、[TRAINING_DATA.md](TRAINING_DATA.md) |

电脑可以安装到 `E:\Xiayuluo`，训练数据默认仍在 `%LOCALAPPDATA%\Xiayuluo\training`；首次可明确选用独立的 `E:/XiayuluoData/training`。已登记位置冲突、原盘失联或格式损坏时停止相关操作，不创建空数据代替；旧模型和历史备份不自动搬迁或删除。Windows 备份文件身份校验已修正路径 stat 与句柄 fstat 时间字段差异，构建和覆盖升级结果仍以实际验证为准。

`mobile/` 使用 Expo / React Native，Android、iOS 共用聊天、记忆、任务、设备界面。已有 Android ARM64 测试 APK，见 [ANDROID_INSTALL.md](ANDROID_INSTALL.md)；iOS 尚无 IPA。电脑主机同步默认关闭，使用前需配置受控 HTTPS 地址和一次性配对；电脑保持开机、服务在线，手机才能获得新的模型回复和电脑执行结果。项目沙箱默认只读，修改代码需在电脑明确配置 `workspace-write`。这不是连接手机 ChatGPT App 的内部工作区。

对话支持 Markdown、代码块、列表和表格排版。前端使用官方 [Marked](https://github.com/markedjs/marked) 和 [DOMPurify](https://github.com/cure53/DOMPurify) 的锁定发布版；运行文件随应用提供，聊天时不访问 CDN。用户输入保持纯文本，助手内容经过 HTML 过滤；回复中的远程图片、表单、脚本和事件处理器不被加载。链接只支持明确的 HTTP(S) 地址，点击后在新标签页打开。

## 日常聊天 App

双端启动本次只主动问候一次。原生 App 打开后按已有授权尝试语音待命，麦克风运行后直接说话，不用先呼名；完整识别文字用于 Codex 只读问答，旁人或电视也可能被识别。停止/结束会关麦，拒绝、出错或停止后本次不会不断自动重开。朗读使用系统本地中文声音，普通新回复完整生成后默认朗读，独立开关和停止入口；不读历史、失败或待确认内容。系统权限、语音依赖、在线电脑和 CLI 登录仍是前提，双端长时间后台及回声未真机验收，不能保证时时刻刻可用，见 [VOICE.md](VOICE.md)。

电脑聊天栏有独立「鼠标控制」开关。开启后，对本轮明确文字要求截取主屏幕，交给所选 Codex/视觉 API 规划并执行一次点击、移动或滚动；没有键盘输入、拖拽、多屏及连续任务闭环。普通聊天不为鼠标功能新截图；另外开启活动感知时，可利用既有短期摘要作不可信背景。手机和语音不继承本机鼠标授权，训练原型不替换现有执行器。Windows 同一登录会话的 GUI 单实例会唤起已有窗口。

默认 `APPROVAL_MODE=auto`：普通 Codex 问答和已配置工具按权限直接调用，电脑设置可切回逐次确认；手机遵循电脑策略，不再要求每轮到电脑批准。自动模式不绕过系统权限、设备配对、工作区指纹或 CLI 沙箱，见 [CODEX_CHAT.md](CODEX_CHAT.md)。

Windows 使用 `Xiayuluo-Setup.exe` 图形安装器、桌面/开始菜单入口直接打开；E 盘存在时默认 `E:\Xiayuluo`，无需 CMD/Python。源码开发入口与首次依赖安装说明在下节；它不是安装包。两端「下载更新」检查固定签名清单后在 App 内下载校验，Windows 重启替换，Android 仍须系统确认，不再跳 GitHub。公开仓库后仍需发布完整签名包，首次仍要取得一个支持该能力的安装包，见 [DESKTOP.md](DESKTOP.md)、[UPDATES.md](UPDATES.md)。

默认界面采用简洁的聊天布局：左侧会话历史与搜索、中间对话、底部输入框。首页可直接开始日常聊天、Codex 编程或记忆管理，鼠标操作使用聊天栏独立开关；回复支持流式显示与复制，历史会话可以切换和删除。未连接模型时，首页显示设置入口，而不会声称模型已可用。

它参考常见聊天助手的交互习惯，保留夏鱼洛自己的视觉设计；不是 ChatGPT 或豆包的客户端，不读取它们的 App 登录。可使用已登录的官方 Codex CLI，或在本机 `.env` 中配置模型 API；云模块及其未部署、未接完的边界见 [CLOUD_SYNC.md](CLOUD_SYNC.md)。旧版本的唤醒词、跳浏览器下载和手机逐次批准描述属于历史行为，不代表本段当前行为。

## 可选开发者模式

在「设置」开启「开发者模式」，切换到深色工作台，显示当前运行状态、首段耗时、总耗时、实际 SSE 事件数和输出字符数；右侧 Inspector 提供运行配置、四级记忆计数、最近一次召回详情和最近事件。窄屏时可以用顶栏按钮打开或关闭检查面板。默认关闭，日常界面不展示日志、参数 Schema 或技术状态栏。关闭开发者模式会同时停止文本日志记录并移除已有日志中的文本。偏好仅保存在该窗口的 WebView 存储中，桌面 App 重新启动后不保证保留。

「事件日志」支持按 HTTP、SSE、工具操作和错误筛选、展开 JSON、清空和导出。日志仅保存在当前窗口内存中，最多保留 300 条；刷新后清空，不写入记忆数据库。默认只记录元数据，勾选「记录后续事件的文本内容」后才保留后续对话/工具文本；取消勾选会移除已有日志中的文本。密钥、认证头和截图始终被过滤。

指标是**浏览器实际观测值**：首段耗时从发起流式请求计算到接收第一段非空文本，包含网络与本地处理；SSE 事件数不是模型 token 数。日志不包含模型隐藏推理或 Codex 内部思考。开启开发者模式后，工具参数 Schema 在「工具箱」中查看，平台、Python、接口地址和沙箱等元数据在「设置」中查看；配置存在不代表已经通过真实连接验证。

## Windows 源码开发启动

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
