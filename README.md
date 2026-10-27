# 夏鱼洛 · 你的日常桌面助手

Windows 优先的个人测试 App：独立窗口、日常对话、Codex 编程、聊天内鼠标控制、四级记忆，另有 Android 客户端。默认自动管理内部服务，不需要手动打开浏览器；调试功能收进可选开发者模式。

## 当前开发状态

当前源码 **0.1.16 / Android versionCode 17**。源码仓库公开，Windows 测试安装包已构建；正式在线发布暂缓，不替换签名或绕过校验。没有替你部署云服务、上传私人数据或生成新安卓 APK。构建/自动化测试不等于真机验收。

| 能力 | 当前范围 |
| --- | --- |
| 对话与 Codex | API 可流式回复；官方 Codex CLI 可使用 ChatGPT 登录，无需模型 API 密钥，不保证逐字输出；不读取 GPT/Codex App 登录 |
| 手机直连电脑 | HTTPS 配对后共享聊天、记忆和绑定工作区的任务；电脑需保持在线，无手机鼠标遥控 |
| 独立云工作区 | 可自部署文本同步、离线存储和明确的电脑任务回执；普通云消息仅同步、不自动回复，云语音未实现，见 [CLOUD_SYNC.md](CLOUD_SYNC.md) |
| 活动感知 | Windows 独立开关，默认关闭；周期观察、短期摘要和概率建议，不理解全部活动，见 [ACTIVITY.md](ACTIVITY.md) |
| 视觉动作学习 | 自研 Neuro 思路的“画面＋目标”离线训练/评测，不自动采集、不执行真实鼠标，见 [VISION_LEARNING.md](VISION_LEARNING.md) |
| 更新与数据 | App 内校验下载安装；仍需正式签名 Release。训练目录与程序分离，更新前校验备份，见 [UPDATES.md](UPDATES.md)、[TRAINING_DATA.md](TRAINING_DATA.md) |

公开 GitHub 加密备份与云同步不是同一能力：仅本人显式选择文件、本机加密后上传，明文最多16MiB，密钥不上传；不是自动备份或完整训练云盘，公开历史不可保证撤回。发布阻塞见 [更新配置](UPDATES.md#015-客户端完善与发布阻塞)。

## 日常聊天 App

Windows 从成功的 [构建页面](https://github.com/XiaYuLuo22/xiayuluo/actions/workflows/windows-app.yml)下载 Artifacts 中的 `Xiayuluo-Windows`，解压运行 `Xiayuluo-Setup.exe`，以后直接打开桌面/开始菜单“夏鱼洛”。E盘存在时默认 `E:\Xiayuluo`，可改目录，不需要 CMD/Python。安装与用户数据路径见 [DESKTOP.md](DESKTOP.md)。

Android 安装记录见 [ANDROID_INSTALL.md](ANDROID_INSTALL.md)，手机配对和构建见 [MOBILE.md](MOBILE.md)；iOS 尚无 IPA。手机主聊天仍直连电脑；设备页的独立云面板需要另行部署可信 HTTPS 服务，不会把 GitHub 仓库当作实时云服务器。

日常界面含会话历史/搜索、对话、输入框和设置，支持 Markdown、代码、列表、表格与复制。运行文件随应用提供，不访问 CDN；助手内容经 DOMPurify 过滤，不加载远程图片、脚本或表单，用户输入为纯文本。

默认 `APPROVAL_MODE=auto`：已配置工具按权限直接调用，电脑可切回 manual；不绕过系统权限、配对、工作区校验和 CLI 沙箱。停止任务不会撤销已发生的文件/鼠标操作。

双端启动一次本地问候；原生 App 按已有授权尝试可见语音待命，无需呼名。停止、拒绝或失败后本次不自动重开；普通新回复可由系统本地中文 TTS 朗读，有独立开关/停止入口。语音仅用于 Codex 只读问答，不继承编程/鼠标权限；后台、回声与麦克风仍需真机验收，不能保证时时可用，见 [VOICE.md](VOICE.md)。

## 可选开发者模式

设置中开启深色工作台，查看运行配置、四级记忆、召回详情和实际流式指标。首段耗时包含网络与本地处理，SSE事件数不是模型 token 数，不显示模型内部思考。

事件日志最多300条，仅当前窗口内存保存；默认只有元数据，显式勾选后才记录后续文本，取消或关闭开发者模式会移除已有文本。密钥、认证头和截图始终过滤；刷新清空，偏好仅在当前 WebView 存储，不保证跨启动保留。

## Windows 源码开发启动

源码入口不是日常安装包。安装64位 Python 3.12（3.11+可用），完整解压后双击 `start.cmd`：首次创建 `.venv` 并安装依赖，后续检查通过会跳过安装；缺少 uvicorn 等依赖会尝试修复，失败保留窗口说明原因，不覆盖 `.env` 和记忆。不要同时开多个安装窗口。

手动安装与启动：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -c requirements.lock -e ".[app,desktop]"
.\.venv\Scripts\python.exe -m assistant
```

首次在没有 `.env` 时创建空白配置，修改配置后重启。Python、WebView2 和 Codex CLI 的系统准备见 [DESKTOP.md](DESKTOP.md)；应用不会自动安装系统软件。构建 Windows exe/安装器用 `build-app.cmd`。

若只用 Codex，按下节登录，不必填 API 密钥。选择模型 API 时在本机 `.env` 配置：

```dotenv
OPENAI_API_KEY=你的模型密钥
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4.1-mini
DESKTOP_ENABLED=true
CODEX_WORKSPACE=C:/Users/you/projects/my-project
CODEX_SANDBOX=read-only
```

模型需支持 OpenAI 兼容流式 `/chat/completions`；工具调用需 function calling，截图理解需视觉能力。本地兼容服务也可使用。环境变量 `ASSISTANT_MODEL_KEY` 优先于 `OPENAI_API_KEY`；不要提交真实密钥。模型未连接时仍可管理记忆和使用已配置的本机能力。

## Codex

安装 Node.js 22+ 后：

```powershell
npm install -g @openai/codex
codex login
```

在设置选择 Codex 问答即可复用官方 CLI 的 ChatGPT 登录；不需要先打开 GPT/Codex GUI。登录诊断不等于网络、会员额度或真实回复可用，详见 [CODEX_CHAT.md](CODEX_CHAT.md)。

编程设置 `CODEX_WORKSPACE` 为已有项目，选择输入栏「Codex 编程」。默认只读；需修改代码时在电脑明确设 `CODEX_SANDBOX=workspace-write` 并重启。auto 模式直接执行，manual 才逐次批准；不使用禁用沙箱/绕过审批选项。CLI 提示 Windows 沙箱未初始化时先按官方引导处理。

任务状态随 CLI JSON 事件显示，不保证逐字输出。停止会终止本应用启动的 CLI 进程树，已经写入的文件不会自动撤销。登录/网络/沙箱失败时可先在目标目录独立运行 `codex` 排查。

## 聊天内鼠标控制

Windows 聊天栏开启「鼠标控制」后，可发送「请点击设置」「双击文件夹」「移动到搜索框」「向下滚动三格」。每轮一个主屏幕点击/右键/双击/移动/滚动，无键盘、拖拽、多屏或自主连续任务；手机和语音不能开启此权限。

开关每次启动默认关，仅明确的本轮鼠标要求临时截图，交给选定的 Codex/视觉 API，结束清理自有图片，不存聊天/记忆。执行前检查2分钟有效期、屏幕尺寸、坐标与41×41目标变化；不符则拒绝，不猜坐标。关闭取消旧规划，重开不恢复旧动作；左上角 PyAutoGUI fail-safe 保留。

安装版首次空白配置启用桌面驱动，源码需 `desktop` 依赖和 `DESKTOP_ENABLED=true`；不覆盖已有显式禁用。管理员/UAC界面、动画或 DPI 差异可能导致拒绝，必须本机验收，不能用云机替身保证真实操作。

## 四级记忆

层级按保留时长/强化程度划分；事实、偏好、经历、方法是内容类型，不是层级。

| 层级 | 默认规则 |
| --- | --- |
| 临时性 | 最近12条消息、最多18,000字符；闲置2小时或重启清空 |
| 一级 | 用户消息形成原始对话片段，7天未使用退出检索；检索3次晋升二级 |
| 二级 | 一级强化或用户明确保存，每次检索续期30天；不把问题推断为事实 |
| 永久 | 仅由用户明确保存/晋升，不自动到期，可手动降级或删除 |

本机中文二字词/英文词匹配综合相关性、近期与重复使用，最多取6条；尚无向量嵌入、模型总结或睡眠整理。历史聊天、临时上下文、可检索记忆分别保存，过期不等于删除历史。删除会话会删除其自动记忆，手动另存保留；清空会删除聊天/记忆，但不删除外部备份。

源码数据在 `.assistant/memory.sqlite3`，可用 `ASSISTANT_DATA_DIR` 修改；发行版位置见 [DESKTOP.md](DESKTOP.md)。SQLite **未加密**，备份需关闭服务或使用一致性导出；不建议记录敏感凭据。对话会发送近期上下文和相关记忆给所选模型服务。

## 新增能力

插件接口为 `assistant/plugins.py`，在 `assistant/tools.py` 的 `build_registry()` 注册。提供名称、说明、严格 Pydantic 参数模型、可用性检查和异步 `execute()`，界面会渲染 catalog 与参数表单；按当前 auto/manual 策略执行，不能省略参数/权限检查。

插件是可信本机 Python 代码，不安装不可信来源。新增依赖、权限和配置应说明并记录许可，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 开发与测试

Windows 安装测试依赖后运行 `python -m pytest -q`；Linux 可用：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -c requirements.lock -e '.[test]'
.venv/bin/python -m pytest -q
.venv/bin/python -m assistant --serve
```

浏览器测试需 `.[test,browser]` 与本机 Chromium，或执行 `python -m playwright install chromium`；可用 `ASSISTANT_BROWSER` 指定浏览器，未安装时明确跳过。

手机开发：

```bash
cd mobile
npm ci
npm run typecheck
npm test
```

测试使用受控模型流/桌面替身，不需要真实 API 密钥，不点击真实桌面。App 只接受本机 Host、同源和会话令牌；保持回环监听，不将控制接口直接暴露公网。

## 开源依赖与临时文件

依赖来源与许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。前端运行文件可用 `python tools/vendor_frontend.py --check` 离线检查；缺失时脚本仅从官方锁定 npm 归档恢复并校验，不执行安装脚本。

只清理确认的构建缓存、临时下载/解包目录、`.pytest_cache/` 和源码字节码。保留配置、聊天、训练/模型/历史备份、签名材料、锁文件、许可证、运行中的 `.venv/` 和 editable 安装的 `.egg-info/`。训练默认在程序目录外的 `%LOCALAPPDATA%\Xiayuluo\training`，位置冲突或磁盘失联时停用相关操作，不建空数据代替；不要整体删除 `dist/` 或 `.vision-learning/`。
