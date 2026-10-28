# 夏鱼洛 · 手机直连与开发

## 当前范围

`mobile/` 是 Expo / React Native源码，Android、iOS共用界面与协议，不是手机 ChatGPT客户端或桌面网站封装。当前源码 **0.1.18 / Android versionCode 19**；最近实际安卓包仍为 **0.1.12 / code13**，安装、大小及校验集中在 [ANDROID_INSTALL.md](ANDROID_INSTALL.md)。没有新 APK、iOS IPA或真机验收。

主聊天采用电脑主机模式：手机经 HTTPS配对访问同一份会话/四级记忆，Codex在电脑指定项目执行。开发者模式是同一本机服务的界面，不是跨主机数据库自动合并。电脑须开机、不休眠、联网且服务运行；同连 Wi-Fi或只装 APK不会自动连接。

0.1.16已有独立云面板、持久离线文本及电脑云任务执行源码，但服务未部署，也未进入现有旧 APK。它与主聊天直连分开，普通云消息只同步、不自动回复，云语音未实现；不自动上传私人数据或创建托管账号，详见 [CLOUD_SYNC.md](CLOUD_SYNC.md)。

0.1.17安卓源码在设备页增加系统中文离线音色、相对语速与音量设置，复用现有唯一TTS引擎，不克隆、下载声音或开启麦克风。手机和电脑音色独立，不保证相同声纹；旧APK没有这些新原生方法，需新版安装包才能使用，见 [CONTROL_RUNTIME.md](CONTROL_RUNTIME.md)。

## 配置电脑与配对

以下在本人电脑操作，不会替你开公网端口、修改防火墙或部署服务器。

1. 安装/更新完整电脑 App，保留配置和数据；源码启动见 [DESKTOP.md](DESKTOP.md)。电脑 `.env` 设置：

   ```dotenv
   MOBILE_ENABLED=true
   ASSISTANT_PORT=8765
   ```

2. 若需编程，选择已有目录并设置 `CODEX_WORKSPACE=E:/XiayuluoWorkspace`，与 App安装盘无关。安装官方 Codex CLI并 `codex login`，见 [CODEX_CHAT.md](CODEX_CHAT.md)。无需预先打开 GPT/Codex桌面窗口；登录检测不证明网络或额度可用。
3. 默认 `CODEX_SANDBOX=read-only`；确需改代码时由本人在电脑设为 `workspace-write`并重启。默认 auto直接执行明确任务，电脑已保存的执行策略优先，可改回 manual；不绕过系统权限、配对或 CLI沙箱。
4. 打开 App，源码可双击 `start.cmd`。手机模式使用固定 `127.0.0.1:8765`回环端口，未启用时 GUI用随机端口。只运行一份服务，不同时启动桌面 App与 `--serve`。
5. 配置自己掌控的域名、证书与 HTTPS反向代理，仅发布 `/mobile/`，见 `deploy/Caddyfile.example`。不要公开整个8765端口、根路径 `/`、`/api`、`/api/bootstrap`或截图接口；不关闭 Host/同源检查或扩大代理头信任范围。
6. 电脑「设备与同步」生成一次性、5分钟有效邀请，手机「设备」填写 HTTPS地址、名称和邀请配对。电脑可撤销设备；手机解除配对只移除本机凭据。失败先查电脑在线、证书、代理路径及 `X-Forwarded-Proto`，不要分享令牌排障。

设备权限位于数据目录的 `devices.sqlite3`，不是另一份记忆库；邀请仅在内存，重启失效。设备令牌30天到期，电脑仅存 SHA-256哈希，手机存在 SecureStore、不写浏览器存储。撤销后后续请求被拒绝，但不能撤回已发生的操作。

## 手机操作与隐私

- 聊天：日常/Codex模式、SSE逐段显示、停止、历史搜索。回前台立即刷新，在线且未生成时每5秒刷新会话/记忆/任务；其他端读取已保存结果，不直播每个生成字符。
- 工作区：手机「任务 / 设备」与电脑核对编号后再发指令。名称/编号/沙箱用于识别，不是凭据，不向手机返回完整路径。主机/目录/沙箱变化后旧任务不能转到新目录，须核实后重新提交。
- 执行：auto无需逐条到电脑确认；manual须在电脑批准，待确认请求2分钟过期。任务卡消失不等于成功，结果在原会话查看。停止/断线可能已有部分改动，不自动重发或回滚。
- 记忆：临时性、一级、二级、永久由电脑管理。临时上下文最多12条，2小时闲置/重启清空，不落手机磁盘；一级7天、检索3次晋升，二级30天且检索续期，永久化只来自用户明确保存。继续旧对话需选择同一会话，不强制两端页面同步。
- 语音：原生 App在配对在线、前台、模型/系统权限就绪后尝试一次监听，先问候、无需唤醒词。拒绝、出错、停止后本次不反复自启。识别文字仅用于 Codex只读问答，播放用系统本地中文声音；不录盘/上传环境音，但文字会发给所选服务。后台和回声未真机验收，见 [VOICE.md](VOICE.md)。

手机无远程批准、执行策略修改、截图、鼠标控制、悬浮窗或读取其他 App的权限；语音不因 auto取得编程、鼠标或记忆写入权限。电脑聊天栏鼠标开关与手机授权无关。

主聊天草稿只在当前运行页面暂存，退出不保证恢复，没有离线自动重发；独立云面板另有持久队列。电脑 SQLite可能含明文私人内容，可结合 BitLocker与独立备份保护；配对手机属于同一所有者，可读全部会话/记忆，不是多用户隔离。API密钥和 Codex登录留在电脑，但推理会发送上下文/召回记忆；本地语音识别不等于问答离线。

## 开发与构建

需要 Node.js 22+；依赖匹配 Expo 58.0.14官方清单，锁在 `mobile/package-lock.json`并保留 npm integrity。Android需已安装工具链，iOS需 macOS/Xcode；EAS仅为本人选择时的方案，不默认上传源码。

```powershell
cd mobile
npm ci --ignore-scripts
npm run typecheck
npm test
npm start
```

Metro仅监听 localhost，真机调试用可信 USB/本机连接，不公开开发服务器，不让有设备凭据的手机加载陌生 JS。`npm run preview:web`只检查布局、禁用配对；`npm run export:android`生成 Hermes/JS资源，**不是 APK**。

Android工具链来源/校验见 `tools/android-toolchain.lock.json`，使用 JDK21及 JDK17、官方 SDK/Gradle/NDK，保留 TLS、主机名及发行包校验。工具链已安装时，从仓库根目录运行：

```bash
cd mobile
npm ci --ignore-scripts --no-audit --no-fund
EXPO_OFFLINE=1 EXPO_NO_TELEMETRY=1 CI=1 npm exec expo prebuild -- --platform android --no-install
cd ..
.venv/bin/python tools/build_android.py --sdk /path/to/android-sdk --java /path/to/jdk21 --java17 /path/to/jdk17 --gradle /path/to/gradle/bin/gradle --signing-dir .android-signing --require-existing-signing --output /path/to/new-private.apk
```

更新必须保持 `com.xiayuluo.assistant`包名、原证书及递增版本码，不生成替代身份或换 debug key。保留 `.android-signing/`、`.release-signing/`与口令，不提交/输出/打包；脚本拒绝覆盖已有 APK。checkout不含环境专用工具链；若当前云机保留 `/workspace/.build-tools/android/run_build.py`适配器，可传新的绝对输出路径使用，新环境仍须验证 CA、网络和工具链。

分发前核验原签名、实际包名/版本/架构、CRC/16KB对齐、内嵌 JS与许可，不含 `.env`、密钥或聊天/设备数据库。设备页「GitHub 一键更新」由App自动从固定GitHub Releases下载校验后交系统安装，不跳转网页；无需配对电脑在线或额外更新服务器，仍须系统确认。正式签名Release尚未就绪，源码推送不能直接更新已安装App，见 [UPDATES.md](UPDATES.md)。构建和受控CLI/浏览器测试不等于真机、真实网络/账号、覆盖升级、生产安全或商店验收。

## 依赖告警

旧记录中 `npm audit --omit=dev`为22项（15高危、7中危），涉及 Expo/Metro/Xcode等上游开发工具；这是历史快照，**不是本轮新审计**。Expo可能将工具链列为生产依赖，不能据此认定全部进入运行包，也不能忽略。此前没有已验证的兼容修复，未执行会降到不兼容 Expo44的 `audit fix --force`；正式发布前须重新审计、跟进上游、检查实际包依赖并完成安全/真机验收。旧版本逐项构建记录由 Git历史保留。
