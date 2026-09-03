# Changelog

本文件记录项目的重大变更、安全修复和架构调整，供后续维护者和 Agent 参考。

---

## [2026-09-03] 安全审计与修复

> **触发**：由独立 Agent 对项目进行代码安全审计 (Conversation `616f4ca7`)，产出修复指令后由另一 Agent 执行 (Conversation `88a44da8`)。
>
> **提交**：`0830c34` on `main`

### 审计发现与修复清单

#### 1. 🔴 Git Remote URL 泄露 PAT (Critical)

- **问题**：`.git/config` 的 `origin` remote URL 中直接嵌入了 GitHub Personal Access Token (PAT)，形如 `https://github_pat_XXXX@github.com/hardass/ZhihuPipeline.git`。任何能读取 `.git/config` 的人/进程都可以获取该 Token。
- **修复**：将 remote URL 切换为 SSH 协议 `git@github.com:hardass/ZhihuPipeline.git`。
- **附带操作**：
  - 通过 `gh ssh-key add` 将本机 `~/.ssh/id_ed25519.pub` 公钥注册到 GitHub 账号（title: `MacBook-ed25519`）。
  - 需要先执行 `gh auth refresh -s admin:public_key` 补充 OAuth scope 才能完成此操作。
- **后续建议**：被泄露的 PAT (`github_pat_11AALPQAI0...`) 应在 GitHub Settings → Developer settings → Personal access tokens 中 **revoke**。

#### 2. 🟡 config.py 硬编码个人身份信息 (Medium)

- **文件**：`src/zhihu_pipeline/config.py` (L164-165)
- **问题**：`GitConfig` 的 `user_name` 和 `user_email` 字段使用了开发者个人信息作为最终 fallback 默认值 (`"hardass"` / `"hardas.yang@gmail.com"`)。如果其他人 fork 或部署此项目，会不知不觉使用这些身份提交。
- **修复**：将最终 fallback 改为空字符串 `""`。配置优先级链不变：`环境变量 → config.yaml → ""`。

```diff
- user_name=str(os.environ.get("GIT_USER_NAME", git_data.get("user_name", "hardass"))),
- user_email=str(os.environ.get("GIT_USER_EMAIL", git_data.get("user_email", "hardas.yang@gmail.com"))),
+ user_name=str(os.environ.get("GIT_USER_NAME", git_data.get("user_name", ""))),
+ user_email=str(os.environ.get("GIT_USER_EMAIL", git_data.get("user_email", ""))),
```

#### 3. 🟡 safe.directory 通配符过度宽松 (Medium)

- **文件**：`src/zhihu_pipeline/git_sync.py` (L30)
- **问题**：`git config --global --add safe.directory "*"` 将全局所有目录标记为安全，绕过了 Git 的 dubious ownership 保护机制。在容器环境中虽然方便，但也使得容器内任何恶意目录都能被 git 操作接受。
- **修复**：将通配符替换为具体的 `vault_path`，仅信任实际使用的仓库目录。

```diff
- _run_git_cmd(["git", "config", "--global", "--add", "safe.directory", "*"], cwd=vault_path)
+ _run_git_cmd(["git", "config", "--global", "--add", "safe.directory", vault_path], cwd=vault_path)
```

#### 4. 🟡 Playwright 进程泄漏 (Medium)

- **文件**：`src/zhihu_pipeline/sync_engine.py` (L319-323, L359)
- **问题**：`run_sync()` 和 `check_auth()` 方法在清理时只调用了 `context.close()` 关闭浏览器上下文，但没有调用 `playwright_instance.stop()` 停止 Playwright 底层的 Node.js 驱动进程。在 Docker 容器中长期运行时，会导致 Playwright Server 进程泄漏、内存持续增长。
- **修复**：在 `context.close()` 前通过 `getattr(context, '_playwright_instance', None)` 获取 Playwright 实例引用，关闭 context 后再调用 `playwright_instance.stop()`。

```diff
  finally:
      try:
+         playwright_instance = getattr(context, '_playwright_instance', None)
          await context.close()
+         if playwright_instance:
+             await playwright_instance.stop()
      except Exception:
          pass
```

> **注意**：`_playwright_instance` 是一个非公开属性，依赖 Playwright 内部实现。如果未来 Playwright 版本修改了此属性，此处会静默跳过（`getattr` + `if` 保护），不会影响主流程。更稳健的做法是在 `get_browser_context()` 方法中显式保存 `playwright` 对象的引用。

#### 5. 🟢 git pull --rebase 失败缺乏恢复逻辑 (Low)

- **文件**：`src/zhihu_pipeline/git_sync.py` (L54-70, 即 `git_pull` 函数)
- **问题**：原 `git_pull` 使用 `--rebase` 拉取，但如果 rebase 因冲突或上次崩溃中断而失败，函数直接返回 `False`，不做任何清理。后续的 `git push` 可能因为仍处于 rebase 中间状态而永久卡死。
- **修复**：
  1. Pull 前先尝试 `git rebase --abort`，清理上次运行可能遗留的中间状态。
  2. 如果 `--rebase` 失败，先 abort 当前 rebase，再用 `--no-rebase`（merge 策略）重试一次。
  3. 两种策略都失败才最终返回 `False`。

### 受影响文件

| 文件 | 改动类型 |
|------|----------|
| `.git/config` | remote URL 从 HTTPS+PAT → SSH（不进版本控制） |
| `src/zhihu_pipeline/config.py` | 移除硬编码 PII |
| `src/zhihu_pipeline/git_sync.py` | 收窄 safe.directory + 加固 git_pull |
| `src/zhihu_pipeline/sync_engine.py` | 修复 Playwright 进程泄漏 |

### 未修改的文件（及原因）

- `config.yaml`：本地运行配置，不进 git（`.gitignore`）。其中已正确配置了 `user_name` / `user_email`，不受 Fix 2 影响。
- `config.example.yaml`：模板文件，占位符已经是正确的示例值，无需改动。
- `Dockerfile` / `docker-compose.yml`：本次审计未涉及容器配置层面的问题。
