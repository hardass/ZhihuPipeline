# Changelog

本文件记录项目的重大变更、安全修复和架构调整，供后续维护者和 Agent 参考。

## [2026-10-08] Attachment prefix reverted: notes get moved, folders cannot gate them

The dedicated `assets/知乎附件/` prefix (and its migration tool, `check-attachments`
audit and node preflight) was removed the same day it landed.

- Rationale: this vault already contains 399 notes the user moved out of the
  collection directory into curated folders. A prefix scoped to where the pipeline
  drops files cannot follow notes the user relocates, and LiveSync's allow-list is a
  path regex applied in both directions - so filtering by prefix would silently skip
  relocated notes while not filtering is the simpler, honest behaviour.
- `images.py` writes to `assets/<note title>/` again (path output verified byte-for-byte
  identical to the pre-change version, now via `attachment_dir`/`attachment_link`).
- The node runs without `syncOnlyRegEx`; it still ignores `.git`, `.obsidian` and
  `manifest.json`.
- Already-migrated directories were deliberately left in place: they are internally
  consistent and rendering correctly on every device, and reverting them would churn
  ~3400 renames across three syncing clients for no functional gain.
- Follow-up defect recorded in AGENTS.md: `../../assets/...` links are depth-dependent
  and break when a note moves to a one-level folder; the fix is vault-resolved embeds.

## [2026-10-08] Node topology: whole-vault mount, `daemon` mode

Settled the two questions that decide the rest of the cutover.

- The node watches `/share/homes/hardass/zhihu-pipeline/notes` — the directory the
  pipeline already writes into — instead of the Zhihu-only `zhihu-vault`. With no
  path allow-list the mount *is* the scope, and reusing the existing full-vault
  directory costs no extra copy (329GB free on the data volume; the old 512m note
  was about RAM, not disk).
- Mode is `daemon`, not the cron `mirror`+`sync` loop. `mirror` deliberately does not
  propagate deletions, which would leave notes the user deleted or relocated on the
  NAS and re-push them over every device. `daemon` propagates deletions both ways;
  the accepted trade-off is that a file missing on the NAS also reads as a deletion,
  so other devices must not edit during the first reconciliation pass.
- Measured while doing this: `remote-status` needs a *stored remote configuration* and
  fails with `Failed to temporarily activate remote configuration` when the container
  cannot write `node-data` (owned by `admin`, mode 700) — compose now runs as root.
  And the node's real key in the milestone doc is the CLI-generated
  `headless-vault-<hash>`; `deviceAndVaultName` is ignored, so `livesync.node_name`
  must be that hash or the heartbeat check always reads the node as stale.

## [2026-10-08] Incident: the vault repo had become the only thing on the Mac's disk

While measuring how far the NAS directory diverges from CouchDB before starting the
node, the comparison came out backwards: 12631 file documents in the database against
6760 files on disk, and **zero untracked files on the Mac** (`git ls-files` 6774 vs 6777
on disk). The 2026-10-07 23:36 untracking commit had therefore been followed by a
destructive checkout/clean, which deleted everything git never tracked:

- every plugin's `main.js`/`manifest.json` — Obsidian on the Mac cannot load
  Self-hosted LiveSync or Dataview any more, and had no sync path at all since then;
- ~5900 files that only ever lived in LiveSync: `personal assets/` (2381), `onenote/`
  (958), `assets/` attachments (3410), `Jobs/` (67).

The data is not lost — it is in CouchDB and on the other devices — so the repair is to
put the Mac back into the sync group and let it pull.

- Restored `obsidian-livesync` **1.0.35** (`main.js`, `manifest.json`, `styles.css`) from
  the official release, matching the node's `1.0.35-cli`; settings must come from a
  Setup URI on a healthy device rather than from git history, which only holds the
  1.0.21-era `data.json`.
- Delivery paused deliberately: `git.sync_mode` → `none` in place (inode preserved), so
  the pipeline cannot consume the Zhihu inbox while nothing publishes it.
- Ignore list extended with `/\.obsidian/` and `\.DS_Store$`; the vault contains nested
  Obsidian vaults (`Jobs/`, `Travel/Japan202606/`, `Investiment/`) whose editor state,
  plugins and `github-sync.log` would otherwise all become documents.
- Measured for future reference: CouchDB keys are lowercased vault-relative paths, the
  original casing lives in `metadata.path`, and `doc_count` (66598) counts 53967 content
  chunks, not files — never treat it as a note count.

## [2026-10-08] Loss assessment corrected: 38 files, not 5900

The first estimate above was wrong and the corrected numbers matter for every later
decision, so they are recorded here as measured.

- Compared the live document set against the Mac disk case-folded: 12,631 file documents,
  6,722 present, and of the 5,909 absent, **5,860 are stale duplicates** - the same content
  under a path nobody uses any more (`assets/<title>/…` from before the 知乎附件 move,
  `personal assets/assets/…` from files later relocated into `assets/`, and `xxx 1.md`
  copies). Those moves were made in git/Finder, so LiveSync never saw them and the old
  documents were never deleted.
- Real gap: **38 content files** (4 Zhihu notes + 10 of their images, 12 `Jobs/`, 6
  `OneNote/`, 3 `personal assets/`, `**While 的用法**.md`, `assets/仅一个文件，暴涨了.md`)
  plus ~10 known junk documents and the one note the user deleted on purpose.
- Every one of the 6,716 files the node could materialize is already on the Mac disk
  (export-minus-disk gap = 0), so the export was never the recovery path for these.
- Two CLI behaviours found the hard way: repeated `sync`+`mirror` rounds plateau at 6,716
  because the CLI does not persist the whole changes feed ("Replication result received,
  but not processed automatically in CLI mode" x1354), and `HEAD`/`GET` on a document id
  is not a reliable existence probe here - use `POST /_all_docs` with exact keys instead.
- Rejected as unsafe: pointing `daemon` at an empty directory. `daemon` propagates
  deletions, and its index knows thousands of files, so it could push deletions for files
  that merely are not in that directory. The bounded fetch instead copies node settings,
  sets `syncOnlyRegEx` to the exact 49 target ids and runs `mirror` - the allow-list is
  used here as a blast-radius limit for a one-off fetch, not as a delivery filter.

---

## [2026-10-06] Delivery is verified before the inbox is consumed

The `git.sync_mode: livesync` switch only suppressed the GitHub push and then reported
`git_pushed = True`, so notes were consumed from the Zhihu inbox while nothing published
them. Delivery is now a first-class, verified step.

- Added `publish_probe.py`: read-only CouchDB verification (document existence plus the
  LiveSync node heartbeat in `_local/obsydian_livesync_milestone`). Unreachable, refused or
  stale backends are errors, never "empty" and never "published".
- `sync_mode` is now `git` | `livesync` | `none`, normalised from aliases, overridable with
  `GIT_SYNC_MODE`, and an unknown value fails closed to `none` instead of guessing `git`.
- `livesync` mode no longer contacts GitHub at all: neither push nor the per-run `git pull`
  and `ensure_git_repo` side effects (`git init`, `remote set-url`, `branch -M`, global
  `safe.directory`).
- Inbox consumption is deferred until delivery is proven per note. `remove_after_sync` can no
  longer delete a Zhihu source whose document has not reached the shared vault, and
  cursor-only ("indirect") proof is explicitly not enough to delete anything.
- Manifest records `publish_status` (`pending` / `published` / `failed` / `unverified`) so a
  slow node is distinguishable from a dead one, and `/status` reports the unpublished backlog.
- `manifest.json` can live in a dot-directory (`output.manifest_path`) outside the synced
  subtree, with a one-time automatic migration that keeps existing records.
- Notifications are channel-aware and truthful; scheduler exceptions and unconfirmed
  deliveries now alert through the notify gateway with a one-hour dedup window.
- `config.yaml` is re-read before each scheduled pass, so edits no longer need a container
  restart to take effect.
- Fixed `GIT_ENABLED=false` being parsed as enabled because `bool("false")` is truthy.
- Added regression coverage for fail-closed probing, per-channel publishing, the deletion
  safety valve, and manifest migration.

---

## [2026-09-20] Low-resolution Zhihu video archiving

- Added support for downloading video Pins at the configured quality; `ld` is the default.
- Added per-video size limits, atomic writes, manifest metadata, and Obsidian video embeds.
- Git sync now stages only the Zhihu collection directory and `assets/知乎视频/`.
- Added regression coverage for rendition selection and video metadata extraction.
- Removed deployment-specific repository paths and notification endpoints from public-facing defaults and documentation.

---

## [2026-09-16] Disable Telegram polling in production

- Added a dedicated `worker` entrypoint for scheduled sync without Telegram long-polling.
- Production Compose now runs `worker` with `telegram.enabled=false`.
- Notify Gateway remains enabled for QR-code, login, and failure notifications.

---

## [2026-09-16] Pipeline state and shared-vault hardening

- Corrupt `manifest.json` now fails closed instead of being replaced with an empty manifest.
- Manifest writes use a sibling temporary file, `fsync`, and atomic replacement.
- NAS Git pushes can be scoped to the Zhihu directory so unrelated private-note changes are not staged by the pipeline.
- Git pull/push failures are surfaced instead of being reported as successful synchronization.
- Tagger inference now honors the configured timeout.
- Added a Docker build-context ignore list and regression tests for manifest recovery behavior.

---

## [2026-09-03] 安全审计与修复

> **触发**：由独立 Agent 对项目进行代码安全审计 (Conversation `616f4ca7`)，产出修复指令后由另一 Agent 执行 (Conversation `88a44da8`)。
>
> **提交**：`0830c34` on `main`

### 审计发现与修复清单

#### 1. 🔴 Git Remote URL 泄露 PAT (Critical)

- **问题**：`.git/config` 的 `origin` remote URL 中直接嵌入了 GitHub Personal Access Token (PAT)。任何能读取 `.git/config` 的人/进程都可能获取该 Token。
- **修复**：将 remote URL 切换为 SSH 协议，并从公开文档中删除具体仓库账号和凭据样例。
- **附带操作**：
  - SSH 公钥应通过 GitHub 账户设置或组织管理流程配置。
- **后续建议**：任何曾经出现在 remote URL、日志或文档中的 PAT 都必须立即撤销并重新生成。

#### 2. 🟡 config.py 硬编码个人身份信息 (Medium)

- **文件**：`src/zhihu_pipeline/config.py` (L164-165)
- **问题**：`GitConfig` 的 `user_name` 和 `user_email` 字段使用了开发者个人信息作为最终 fallback 默认值。如果其他人 fork 或部署此项目，会不知不觉使用这些身份提交。
- **修复**：将最终 fallback 改为空字符串 `""`。配置优先级链不变：`环境变量 → config.yaml → ""`。

```diff
- user_name=str(os.environ.get("GIT_USER_NAME", git_data.get("user_name", ""))),
- user_email=str(os.environ.get("GIT_USER_EMAIL", git_data.get("user_email", ""))),
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
