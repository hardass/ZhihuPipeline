# 自动化 Zhihu Pipeline 改造计划 (Handoff to Gemini 3.7 Flash)

## 背景与目标 (Goal Description)
当前 `ZhihuPipeline` 依赖 Mac 本地的 `start_chrome.sh` 拉起带有 GUI 的 Chrome 浏览器，并通过 CDP 进行 Playwright 控制。这在反爬上有效，但不具备真正的自动化能力。
目标：将脚本重构并容器化，部署到用户的 QNAP NAS (amd64) 上的 Docker 环境中，实现 100% 后台静默运行。同时引入 **Telegram 二维码扫码通知流** 解决 Cookie 过期时的重登录问题。

## 执行前确认 (User Review Required)

> [!IMPORTANT]
> **Telegram Bot 配置说明：**
> 用户已经成功配置了 Telegram 机器人，相关的安全凭证（`TELEGRAM_BOT_TOKEN` 和 `TELEGRAM_CHAT_ID`）**已经统一整合到了项目根目录的 `config.yaml` 文件中**。
> 
> *执行 Agent 在编写代码时，请直接从 `config.yaml` 中读取 `telegram.bot_token` 和 `telegram.chat_id` 节点，无需再处理 `.env` 环境变量，切勿要求用户重新提供。*

## 架构设计细节 (Proposed Architecture)

1. **容器化与无头规避 (Xvfb)**：
   - 弃用原有的本地 `--remote-debugging-port` 连接方式。
   - 使用官方包含系统级依赖的镜像，如 `mcr.microsoft.com/playwright:v1.40.0-jammy`。
   - 在 Docker 内部使用 `Xvfb` (X virtual framebuffer) 启动 Playwright (`headless=False`)。由于是有头模式运行在虚拟内存屏幕上，知乎的反爬指纹 (x-zse-96) 不会因为无头模式而触发风控拦截。
   - 使用全局挂载卷 (Volume) 来持久化存放 `user_data_dir`。

2. **Telegram 二维码登录流机制**：
   - 当 `check_login()` 发现用户未登录（重定向到了 `/signin` 或未检测到用户头像）时，不退出，而是进入重登录流。
   - 页面导航至登录页，定位并点击“二维码登录” Tab (如果当前不在该 Tab)，等待 `img.Qrcode-qrcode` 出现。
   - 截取该二维码元素的截图 (`element_handle.screenshot(path="qr.png")`)。
   - 调用 Telegram Bot API (`sendMessage` 配合 `sendPhoto`) 将 `qr.png` 发送到用户的手机。
   - 脚本进入异步 `while True` 轮询检查页面 DOM：如果页面发生了跳转回到首页，或出现了登录成功的标识（右侧头像），则认为扫码成功。
   - 扫码成功后，通过 `user_data_dir` Playwright 会自动保存当前的 Browser Context (Cookies)，脚本发送“登录成功”通知给 Telegram，并平滑切回正常工作流。

## 详细实施步骤 (Implementation Steps for Executing Agent)

### 1. 配置层改造
- 修改 `config.yaml` 模板，增加 Telegram 配置块：
  ```yaml
  telegram:
    enabled: true
    bot_token: "" 
    chat_id: ""
  ```
  *(也可支持读取环境变量作为 fallback)*

### 2. 鉴权逻辑重构 (`src/zhihu_pipeline/auth.py`)
#### [MODIFY] `src/zhihu_pipeline/auth.py`
- 删除 `connect_chrome()` 中对 `localhost:9222` CDP 的硬编码连接。
- 引入新的启动方法，使用 `async_playwright().chromium.launch_persistent_context()`，并必须指定 `headless=False`（重要！为了在 Xvfb 中运行）。
- 增加 `handle_qr_login(page, telegram_config)` 异步函数：
  - 实现截图逻辑。
  - 实现 HTTP POST 到 `https://api.telegram.org/bot<TOKEN>/sendPhoto` 的通知逻辑。
  - 实现在 `while` 循环中的轮询检测（带有合理的 `asyncio.sleep` 防止高频刷 DOM）。

### 3. Docker 容器化文件编写
#### [NEW] `Dockerfile`
- 基于包含 Xvfb 和 Playwright 系统依赖的底层镜像（建议直接用 `mcr.microsoft.com/playwright` 官方包）。
- 安装 `uv` (用于极速依赖安装)。
- 设置工作目录并用 `uv pip install` 根据锁文件安装项目环境。

#### [NEW] `entrypoint.sh`
- `#!/bin/bash`
- 使用 `xvfb-run -a python -m zhihu_pipeline` 的包装命令来启动入口程序，从而向 Playwright 提供虚拟显示层。

#### [NEW] `docker-compose.yml`
- 定义挂载卷映射，例如 `./chrome_profile:/app/chrome_profile` (务必保证持久化，否则每次重启都要扫码)。
- 映射 Obsidian 的输出目录 `output:/app/output`。
- **(重点)** 根据用户的全局规则，目标服务器为 QNAP NAS (amd64)，为解决网络超时，网络模式必须声明为 `network_mode: "host"`。

### 4. 其它依赖与清理
- **[DELETE]** `start_chrome.sh` （不再需要）。
- 修改 `__main__.py` 入口，去掉在连接失败时提示“请运行 start_chrome.sh”的冗余日志，改为优雅地初始化 `launch_persistent_context`。

## 测试与验证计划 (Verification Plan - Mandatory)

> [!WARNING]
> 执行代码前，负责编码的 Agent 必须与用户配合，按以下步骤完成验证：

1. **环境连通性检查**：
   - 使用临时脚本验证传入的 Telegram Token 和 Chat ID 能否成功发送一条 Text 消息。
2. **二维码推流逻辑验证 (Dry Run)**：
   - 故意将 Profile 目录设为一个空文件夹，强制触发 `auth.py` 的“未登录流程”。
   - 检查脚本是否成功保存了一张 `qr.png` 并通过 Telegram 发送给了用户。
   - 暂停执行，等待用户真机打开知乎 App 扫码。
   - 观察脚本后台日志，是否在扫码后正确地打印出了“登录成功”，并且 Context 持久化生效。
3. **架构适配检查**：
   - 确保 `docker-compose.yml` 中具备 `network_mode: "host"`。
   - 确保 `entrypoint.sh` 具有 `+x` 执行权限，并正确调用了 `xvfb-run`。
