# 夏鱼洛 · 一键启动

## 已有旧源码包

1. 先关闭夏鱼洛和旧启动窗口。解压一键启动补丁。
2. 把补丁里的 `start.cmd` 与 `tools` 文件夹复制到你原来的夏鱼洛文件夹，选择替换同名文件。原文件夹中应能看到 `pyproject.toml` 和 `requirements.lock`。
3. 双击原文件夹中的 `start.cmd`。首次安装需要联网，请等待；安装好后会自动打开夏鱼洛窗口。

例如你原来的位置是 `C:\Users\shuang\Desktop\xiayuluo-chat-app-windows-source`，复制后应有：

```text
xiayuluo-chat-app-windows-source\
  start.cmd
  pyproject.toml
  requirements.lock
  assistant\
  tools\
    bootstrap_windows.py
```

补丁不是完整 App，请不要在单独的补丁文件夹中运行 `start.cmd`。不需要重新解压整个旧包，不要删除 `.env`、`.assistant` 或 `.venv`。

## 首次使用完整新版源码包

完整解压后双击 `start.cmd`，不需要先手动输入安装命令。脚本会检查并准备专用 Python 环境与依赖；缺少 `uvicorn` 等依赖时会尝试重新安装。后续检查通过时跳过重复下载。

## 运行要求

- Windows 10/11。需要先安装一次 Python 3.11+，推荐 64 位 Python 3.12；脚本没有权限也不会自动安装系统 Python。若检测不到 Python，窗口会显示官方下载地址。
- 首次安装依赖需要联网；不要同时打开多个安装窗口。失败时窗口会保留报错，安装成功前不会继续启动。
- App 窗口需要 Microsoft Edge WebView2 Runtime；缺失时可从微软官方页面安装。
- 模型连接与 Codex 登录仍需单独配置；本补丁不会自动安装或登录 Codex，也不会更改已有密钥和项目权限。

若失败，可发送启动窗口最后几行报错截图。不要发送 `.env`、API 密钥或登录凭据。

此启动逻辑已在云环境自动化测试；云环境不是你的 Windows 电脑。Windows CMD 与最终桌面窗口仍需在 Windows 本机实际运行确认。
