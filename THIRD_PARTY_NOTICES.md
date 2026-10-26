# 第三方组件

前端仅保留以下官方发布版的浏览器运行文件和许可证，不需要 npm 安装或在线 CDN。

| 项目 | 锁定版本 | 用途 | 许可证 |
| --- | --- | --- | --- |
| [Marked](https://github.com/markedjs/marked) | 18.1.0 | 对话 Markdown、列表、表格和代码块排版 | MIT；原始 LICENSE 另含上游 Markdown 许可说明 |
| [DOMPurify](https://github.com/cure53/DOMPurify) | 3.4.16 | 过滤 Markdown 解析得到的 HTML | Apache-2.0 或 MPL-2.0，保留两份原始许可 |

文件在 `assistant/static/vendor/`。其中 `marked.js` 是官方 `lib/marked.umd.js`；`dompurify.js` 是官方 `dist/purify.min.js`。仅重命名，内容与许可证均未修改。

下载地址、npm SHA-512 integrity、各文件 SHA-256、官方仓库和版本记录在 `tools/frontend-vendor.lock.json`。更新依赖时，必须先核对官方发布内容及许可证，再更新锁定信息并运行浏览器测试；校验失败时应调查下载来源，不能改掉预期校验值来绕过失败。

Python 依赖通过 `pyproject.toml` 和 `requirements.lock` 管理，包括 FastAPI、Uvicorn、HTTPX、python-dotenv，以及可选 PyAutoGUI、Pillow、pytest、Playwright；这些依赖通过发行包安装，没有复制它们的仓库。测试与运行需要的环境依赖保留在 `.venv` 中。

独立的视觉动作学习实验使用官方 [PyTorch](https://github.com/pytorch/pytorch) 2.14.1+cpu（BSD风格许可及随wheel提供的第三方许可）和 [NumPy](https://github.com/numpy/numpy) 2.4.2（BSD-3-Clause），约束记录在 `requirements-vision.lock`。CPU wheel来自 `https://download.pytorch.org/whl/cpu`，NumPy由PyPI发行包安装；没有复制Neuro/Among Us的GPL代码、模型权重或训练数据，模型结构与合成图像生成器为本项目自行实现。实验代码与权重不加入普通App安装包；若以后分发训练运行环境，仍须保留所安装wheel中的完整许可证和第三方NOTICE，不能只用此说明代替。

桌面 App 使用 [pywebview](https://github.com/r0x0r/pywebview) 6.2.1（BSD-3-Clause）；Windows 使用系统 Microsoft WebView2 和 pythonnet 适配层。构建工具为 [PyInstaller](https://github.com/pyinstaller/pyinstaller) 6.22.3，其 GPL 许可附带允许分发所构建程序的特殊例外。构建脚本会收集已安装依赖的许可证到发行包 `licenses/`，并保留此说明。WebView2 Runtime 通过微软的系统安装方式提供，未随源码包复制或修改。

## 离线语音（源码测试功能）

采用官方 [Vosk API](https://github.com/alphacep/vosk-api)：Android Maven 0.3.75、Windows PyPI 0.3.45，Apache-2.0；Android 使用 [JNA](https://github.com/java-native-access/jna) 5.18.1（Apache-2.0 或 LGPL-2.1-or-later，本分发选择 Apache-2.0）。来源及已获取的 Android AAR 校验值在 `tools/voice-sources.json`。JNA 5.18.1 原始 LICENSE 与 AL2.0 保存在 tools/licenses/，随构建复制。R8 保留 JNA 反射和原生符号所需类，构建继续收集原始许可与 NOTICE。

中文模型采用官方 `vosk-model-small-cn-0.22`，已在官方 `https://alphacephei.com/vosk/models` 目录核对 Apache-2.0 许可，并与官方 model-list.json 发布大小/MD5 比对，固定归档 SHA-256。Vosk 上游原始 Apache 许可保留在 `tools/licenses/vosk-COPYING`，随双端构建复制；模型自带 README 原样保留。构建准备工具保留 HTTPS/哈希验证，不把这些校验冒称上游数字签名。来源与验收见 VOICE.md 和 tools/voice-sources.json。

Windows 可选 [python-sounddevice](https://github.com/spatialaudio/python-sounddevice) 0.5.6（MIT，调用 PortAudio）和 [pyttsx3](https://github.com/nateshmbhat/pyttsx3) 2.99（MPL-2.0，使用系统 SAPI）。Windows 原始许可由打包脚本从实际依赖中收集；系统声音不随源码复制。独立 JVM 状态机测试使用 Kotlin 2.2.10（Apache-2.0）与 JUnit 4.13.2（EPL-1.0），不把测试依赖打进 APK。

## 手机 App 其余开发依赖

更新模块使用 [cryptography](https://github.com/pyca/cryptography) 50.0.2（Apache-2.0 或 BSD-3-Clause）验证 Ed25519，间接依赖 CFFI 2.1.1（MIT）及 pycparser 3.11（BSD-3-Clause），版本锁在 `requirements.lock`，原始许可随 Windows 发行包收集。手机使用 [noble-curves](https://github.com/paulmillr/noble-curves) / [noble-hashes](https://github.com/paulmillr/noble-hashes) 2.4.0（MIT）作相同签名校验，并使用官方 Expo FileSystem 58.0.9、IntentLauncher 58.0.3、Application 58.1.2（MIT）下载/校验 APK、打开系统安装器及获取已安装版本；精确 npm 来源与 integrity 在锁文件中，原始许可随 APK 收集。

手机源码不复制上游仓库或安装包，依赖经官方 npm 发布获取；精确版本、来源和 integrity 保存在 `mobile/package-lock.json`。运行依赖与原生模块版本以 Expo 58.0.14 的官方 `bundledNativeModules.json` 为依据：

| 上游 | 版本 | 用途与许可 |
| --- | --- | --- |
| [Expo](https://github.com/expo/expo) | 58.0.14 | 原生构建框架与 expo/fetch，MIT |
| Expo SecureStore / StatusBar | 58.0.2 / 58.0.3 | 系统安全存储与状态栏，MIT，同上游 |
| [React](https://github.com/facebook/react) / React DOM | 19.3.0 | 界面运行时及仅布局用的 Web 预览，MIT |
| [React Native](https://github.com/facebook/react-native) | 0.88.0 | 原生跨平台控件，MIT |
| [Safe Area Context](https://github.com/AppAndFlow/react-native-safe-area-context) | 5.10.1 | 手机安全区域，MIT |
| [React Native Web](https://github.com/necolas/react-native-web) | 0.21.4 | 仅开发预览，MIT |
| [TypeScript](https://github.com/microsoft/TypeScript) | 7.0.2 | 类型检查，Apache-2.0 |

手机安装包分发时需要随产物保留上述依赖及间接依赖所要求的许可证，不得以此简表替代原始许可。私人 Android 构建脚本收集实际安装的 npm 许可证与 JAR/AAR 中的许可、NOTICE，并保留 Maven POM 的许可来源记录到 APK 的 `assets/licenses/`；工具链本身不打包进 APK。当前 Expo 开发工具链有未解决的 npm audit 告警，验收边界和风险记录在 MOBILE.md；未盲目降级或绕过完整性校验。
