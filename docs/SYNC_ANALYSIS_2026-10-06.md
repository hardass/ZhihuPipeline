# 同步通道断裂分析：GitHub 已停、LiveSync 未接管

分析日期：2026-10-06（Asia/Shanghai）
范围：本机 Mac 工作树、`/Users/hardass/notes` vault、QNAP NAS 容器与 vault、NAS 上的 CouchDB
性质：只读诊断，未修改任何代码、配置或容器

---

## 1. 结论先行

**LiveSync 从来没有被"启动"过，因为这个项目里根本不存在 LiveSync 的实现。**

Hermes 做的改动只是在 `sync_engine.py` 里加了一个 `sync_mode` 开关，而 `sync_mode == "livesync"` 这条分支唯一的作用是**跳过 GitHub push**，并把结果伪造成"已发布"。它不会写 CouchDB，不会启动任何客户端，也不会通知任何设备。

所以现在的实际状态是：

- GitHub 投递：NAS 侧已停（10-05 08:38 是最后一次 auto-sync 提交），Mac 侧本来就没有 auto-push → 仓库冻结。
- LiveSync：CouchDB 服务是活的、Mac 的插件是配置好的、手机也在正常轮询——**但 NAS 上那份 vault 目录没有任何 Obsidian 客户端打开它**，管道写进去的文件永远进不了 CouchDB。
- 结果：管道还在照常下载、照常把"从知乎收藏夹删除"执行掉，新笔记只存在 NAS 磁盘这一份上。

一句话：这是一次"只关不启"的半截迁移，投递通道现在是断的。

---

## 2. LiveSync 为什么不可能自己跑起来（架构层原因）

Self-hosted LiveSync 的两半：

| 半边 | 位置 | 状态 |
|---|---|---|
| 服务端（CouchDB） | NAS 容器 `obsidian-couchdb`（couchdb:3，Up 5 weeks），公网 `obsidian-sync.perilcrosser.com` 返回 401（需要鉴权=服务正常） | ✅ 正常 |
| 客户端（Obsidian 插件） | 必须有一个**正在运行并打开了该 vault 的 Obsidian 实例** | ❌ NAS 上没有 |

关键点：LiveSync 不是文件同步器，它是 Obsidian 的插件，靠 Obsidian 打开 vault 后监听 `modify`/`create` 事件，再把变更编码成自己的文档写进 CouchDB。NAS 上 `/share/homes/hardass/zhihu-pipeline/notes` 只是一个被 Docker bind mount 的普通目录，容器里跑的是 Playwright + Python，没有任何 Obsidian 进程。

`docker ps` 全表里和 Obsidian 相关的只有 `obsidian-couchdb` 一个容器——它是数据库，不是客户端。

---

## 3. 代码层证据

| 位置 | 事实 |
|---|---|
| `sync_engine.py:382-399` | `sync_mode` 判断只包住 push；`385-387` 行 livesync 分支只打一行日志并 `git_pushed = True`（伪成功） |
| `sync_engine.py:60-63` | `git_pull` 只看 `git.enabled + auto_pull`，**不受 `sync_mode` 影响**；livesync 模式下每轮仍然去 GitHub 拉取，失败就 `raise RuntimeError` 整轮中止 |
| `git_sync.py:22-51` | 每次 pull/push 前都会执行 `ensure_git_repo`：`git init`、`remote set-url`、`branch -M`、写全局 `safe.directory`——livesync 模式下这些副作用照旧发生 |
| `sync_engine.py:407` | 返回值仍叫 `git_pushed`，语义已经和实际通道不符 |
| `bot.py:133-139` | 据此向用户显示"GitHub 已推送" |
| `bot.py:179-188` | 定时任务的完成通知文案**硬编码**"已自动推送到配置的 GitHub 仓库"，livesync 模式下这句话是假的 |
| `bot.py:203-204` | 调度循环 `except Exception: logger.error(...)`，不告警。GitHub pull 的 fail-closed 异常被静默吞掉，是监控盲区 |
| 全仓 grep | 除注释和 `docs/VIDEO_SYNC_TOPOLOGY_2026-09-20.md` 的描述外，**没有任何 CouchDB/LiveSync 写入代码**（无 HTTP 客户端、无 `_bulk`、无 `minified` 编码） |
| `config.py:96-104, 189-198` | `sync_mode` 只从 YAML 读，没有环境变量覆盖；`config.example.yaml` 的 git 段里根本没有 `sync_mode` 字段，照抄示例会得到默认 `"git"` |
| `tests/test_git_sync.py` | 覆盖 `--rebase` 回退 merge、pathspec 限定 stage；**没有一条 `sync_mode == "livesync"` 用例**，也没有 sync_engine 级别的测试 |
| 代码仓库 `.gitignore` 新增 `知乎收藏/`、`assets/知乎视频/` | 对生产**无效**：这两个路径属于 vault 那个 git 仓库，而 vault 的 `.gitignore` 只忽略 `.obsidian/workspace*.json`。这一改动只是表达了意图，实际什么都没停 |

---

## 4. 现场实测数据

### 4.1 配置与提交时间线

- NAS `/share/homes/hardass/zhihu-pipeline/config.yaml`（mtime 10-05 19:33）：`git.enabled: true`、`auto_pull: true`、`auto_push: true`、`sync_mode: livesync`。即**只有 push 被静音，pull 与 GitHub 依然强耦合**。
- vault 仓库最后一次管道提交：`16e7e44 2026-10-05 08:38 docs: auto sync 2 zhihu note(s) [skip ci]`。之后 GitHub 上再无提交。
- Mac `obsidian-git`：`autoSaveInterval=5`（每 5 分钟本地 commit）、`autoPushInterval=0`（**不自动 push**）、`autoPullOnBoot=true`。也就是说即使 NAS 恢复 push，Mac 端的改动仍需手动 push 才上 GitHub——GitHub 这条通道目前是双向半死。

### 4.2 数据积压与单副本风险

- NAS vault `知乎收藏/*.md`：**288 篇**；Mac：**285 篇**。
- 仅存在于 NAS、Mac/GitHub/CouchDB 三处都没有的 3 篇：
  - `知乎收藏/我的收藏/2026-09-17 该怎么解决GPT5.6过度工程化以及臃肿的无用测试的问题？.md`
  - `知乎收藏/我的收藏/2026-09-28 Jev新玩法：给Obsidian笔记打标签.md`
  - `知乎收藏/我的收藏/2026-09-29 学日语后你染上了什么毛病？.md`
- 直接向 CouchDB 查询这 3 个文档 ID：**全部 404**。`obsidian-vault` 里最新的内容文档停在 `2026-09-24` 那篇；`docker logs`（覆盖 09-22 至今）里对 `obsidian-vault` 的**内容级 PUT 次数为 0**，只有心跳类的 `_local/obsydian_livesync_milestone` 和每 60 秒的 `_changes?since=67295` 轮询。
- 更要命的是：`sync.remove_after_sync` 默认为 true（`config.py:39`，`sync_engine.py:323-335`），条目一下载成功就立刻从知乎收藏夹删掉。**这 3 篇在知乎端也已经不存在了**，NAS 磁盘是宇宙中唯一的副本。

### 4.3 NAS vault 的 git 工作树正在变脏

`git status`（在容器内执行）：

```
 M 知乎收藏/manifest.json
?? 知乎收藏/我的收藏/2026-09-17 ….md
?? 知乎收藏/我的收藏/2026-09-28 ….md
?? 知乎收藏/我的收藏/2026-09-29 ….md
?? assets/<4 个图片目录>
```

最新一轮日志（10-06 12:27）：

```
Git pull --rebase failed: cannot pull with rebase: You have unstaged changes.
→ 回退 merge 策略成功
...
Tagging finished: 0 tagged, 14 failed
Sync mode is 'livesync'; skipping GitHub push to save bandwidth/quota.
```

两个隐患已经成形：

1. `manifest.json` 长期处于"已修改未提交"状态。Mac 的 obsidian-git 每 5 分钟会 commit 整个 vault（含 `知乎收藏/manifest.json`），一旦这些改动被 push 上去，NAS 下轮 pull 就会遇到**同一文件双端修改**，merge 失败 → `git_pull` 返回 False → `sync_engine.py:63` 抛 RuntimeError → **整条管道停摆**，而且按 `bot.py:203` 只会写进日志、不会通知你。
2. livesync 模式下 push 永远不会成功，工作树只会越来越脏，脏到一定程度连 merge-pull 也救不回来。

### 4.4 打标链路同时是断的（独立问题）

`https://qwen-coder-next.perilcrosser.com/v1/models` 现在返回 **HTTP 530**（Cloudflare 隧道/源站不可达），所以每轮固定 `14 failed`。notify gateway 返回 200，正常。这和同步无关，但说明"GB10 已经配好"这件事目前不成立，`docs/NAS_AUDIT_2026-09-16.md` 里的验收结论已经过期。

### 4.5 Mac 的 LiveSync 其实是好的

`data.json` 顶层 `couchDB_URI` 为空、`liveSync: false`，容易误判成"没配置"。实际配置在新版的多 profile 字段里：

```
remoteConfigurations → "CouchDB obsidian-sync.perilcrosser.com"（uri 已 E2EE 加密存储）
```

CouchDB 的 `_local/obsydian_livesync_milestone` 里 `accepted_nodes` 有 3 个节点，其中 `s4um2wpdem` 的 `vault_name: "notes"`、`device_name: "notes-547…"`（`547ad80b1bc0fb6f` 正是本机 vault ID）、`plugin_version: 1.0.21`。

**结论：Mac↔CouchDB↔手机这条路是通的。断的只有"NAS 管道产物 → CouchDB"这一段。**

### 4.6 一个值得警惕的次生风险：两条通道都在搬 `.obsidian`

vault 仓库里 `.obsidian/plugins/*/main.js` 和 `data.json` 都是 **git tracked**（`git ls-files` 可见），包括 `obsidian-livesync/data.json` 和它 3.7MB 的 `main.js`（当前 HEAD 版本 1.0.21）。而 `docs/NAS_AUDIT` 之外，vault 内 `Hermes/2026-09-25 Hermes 双机架构与 Notes 同步方案.md` 记录的插件版本是 **1.1.5**、并提到存在 `encrypted.json`（本机现在没有这个文件）。

这构成一个可复现的危险机制：**git 通道的 pull/checkout 会静默回滚 Obsidian 插件的二进制和配置**。obsidian-livesync 官方 manifest 自己也写着 "Please make sure to disable other synchronize solutions to avoid content corruption or duplication"。LiveSync 配置不知何时被抹平，与这个机制高度吻合（属于推断，不是确证）。只要 git 和 LiveSync 同时存在，这个雷就还在。

---

## 5. 三条路线对比

| 路线 | 做法 | 优点 | 代价 / 风险 |
|---|---|---|---|
| **A. 恢复 GitHub 作为 NAS 的投递通道** | NAS `sync_mode` 改回 `git`；Mac 端 LiveSync 继续负责 Mac↔手机 | 改动量最小（一行配置）、立即止住积压、那 3 篇能自动补回去 | 两条通道同时写同一批笔记，冲突面大；必须先解决 4.3 和 4.6，否则 pull 迟早卡死 |
| **B. 让 NAS 成为真正的 LiveSync 写入者**（符合你原本意图） | NAS 上加一个常驻 Obsidian 容器（headless，例如 linuxserver/obsidian 那类镜像），打开 NAS 那份 vault，用 Setup URI 导入现有 profile + 口令；管道只负责落盘；NAS 的 `git.enabled` 关掉 | 单一通道、手机能直接收到知乎新文章、彻底摆脱 GitHub 体积问题 | 多一个常驻容器与其维护面；要实测 LiveSync 能否稳定采集"外部进程批量写入"的文件；`video_max_size_mb=50` 与 CouchDB `max_document_size` 的边界要重新校准（`docs/VIDEO_SYNC_TOPOLOGY` 已记录 49.15MiB 就被拒收）；口令/profile 需要安全落到容器里 |
| **C. 让管道直连 CouchDB（自研写入）** | 用 Python 直接写 LiveSync 文档格式 | 不需要 Obsidian 容器 | **不推荐**：LiveSync 文档格式（chunks / `h:` 索引 / minified / E2EE）是私有的、随版本变动的，等于自己维护一个不兼容风险极高的 fork |

我的建议：**先无条件做第 6 节的 P0 止血（与路线选择无关），然后按 A→B 的顺序走**：用 A 花 5 分钟把断掉的投递恢复、把积压和 3 篇孤儿笔记收干净，同时把双通道的冲突面拆掉；再把 B 作为目标形态一次性完成收敛，收敛完成后才允许彻底断开 GitHub。反过来先做 B，会在"管道已经停了、新形态还没验完"的空窗里继续丢笔记。

---

## 6. 建议的改动清单

### P0（止血，和路线选择无关，建议尽快做）

1. **救回 3 篇孤儿笔记**：先把它们从 NAS 拷到 `/Users/hardass/notes`（打开 Obsidian 后 LiveSync 会自动分发到手机），或临时 `sync_mode: git` 跑一轮 push 再切回来。无论选哪种，先确认 `obsidian-vault` 里这 3 个文档不再是 404。
2. **消灭"伪成功"**：`sync_engine.py:385-387` 的 `git_pushed = True` 改掉——引入中性的 `published` / `publish_channel` 返回值，livesync 模式下明确返回"未发布"；`bot.py:133-148` 和 `bot.py:179-188` 的文案按通道生成，不能再写死"已自动推送到配置的 GitHub 仓库"。
3. **发布不可用要告警，不能静默**：`bot.py:203-204` 的调度异常（含 pull fail-closed 的 RuntimeError）必须走 `notify`；再加一个"发布心跳"检查——例如 manifest 里记 `last_published_at`，超过 N 小时没有成功发布就推送告警。这次的断裂之所以拖到你来问我，就是因为没有这个告警。
4. **`remove_after_sync` 加安全阀**：`sync_engine.py:323-335` 改成"仅在确认已发布（push 成功，或 LiveSync 侧已可查到该文档）之后才从知乎收藏夹删除"。现在这个顺序是"下载→立刻删源→可能永不分发"，是整套设计里唯一真正会丢数据的地方。
5. **修 GB10 打标链路**：隧道/`gb10-coder-next-vllm.service` 现在 530，14 篇一直 pending。这条独立于同步，但会持续污染日志和验收判断。

### P1（将要做路线选择时一起做）

6. **把 `sync_mode` 变成真的模式开关**，而不只是 push 静音：livesync 模式下应跳过 `git_pull` 和 `ensure_git_repo`（`sync_engine.py:60-63`、`git_sync.py:22-51, 59`），否则 NAS 会一直从 GitHub 拉私人笔记进容器挂载目录，并且工作树越积越脏。
7. **拆掉 git 与 LiveSync 的重叠范围**：把 `.obsidian/plugins/**/main.js`、`.obsidian/plugins/**/data.json`、`.obsidian/` 整体从 git tracked 列表里移出（`git rm --cached` + vault `.gitignore`）。这一步同时解决 4.6 的插件配置被回滚风险，和仓库 2.27 GiB 的体积问题。
8. **`manifest.json` 脱离双通道**：它现在住在 `知乎收藏/manifest.json`（`sync_engine.py:29-30`），既被 git stage（`sync_engine.py:396`）又被 LiveSync 同步。建议挪到 vault 内的点目录（例如 `.zhihu-pipeline/manifest.json`，Obsidian 不展示、LiveSync 不采集），或明确排除在 git 之外，避免"另一端拿到旧 manifest → `is_synced` 判假 → 重复下载或误删已同步文章"。
9. **补 `sync_mode` 的配置面**：`config.example.yaml` 的 git 段加上 `sync_mode` 和注释；`config.py` 里给它加环境变量覆盖（`GIT_SYNC_MODE`），和其余 git 字段保持一致。
10. **补测试**：`tests/test_git_sync.py` 或新增 `tests/test_sync_engine_publish.py`，覆盖 livesync 分支不发网络请求、`published=False`、异常会告警三条契约。
11. **视频体积边界对齐**：`videos.py` 用的是十进制 50,000,000 字节，而 CouchDB `max_document_size` 实测在 51.5M 字节就已拒收（见 `docs/VIDEO_SYNC_TOPOLOGY_2026-09-20.md`）。若走路线 B，这个上限需要下调并加一次"入库探针"验证，否则会出现"下载成功但永不同步"的静默丢失。

### P2（文档与运维卫生）

12. **文档对齐现实**：`README.md`（第 3、20、34、38、83 行附近）仍在讲"NAS 推 GitHub / Mac 用 Obsidian Git 拉"；`docs/DESIGN.md` 完全没有同步章节；`docs/CHANGELOG.md` 没有这次 `sync_mode` 改动的记录；`docs/NAS_AUDIT_2026-09-16.md` 的 GB10 与 GitHub 验收结论已过期。选完路线后需要一次性重写"同步拓扑"这一节。
13. **凭据落盘**：NAS 与本机 `config.yaml` 的 `repo_url` 里内嵌了 GitHub token（`config.py:193` 的 `GIT_REPO_URL` 环境变量已经支持，却没用上）。既然要动配置，顺手换成环境变量，并轮换一次 token。
14. **`.gitignore` 里那两行**（`知乎收藏/`、`assets/知乎视频/`）要么删掉，要么移到 vault 仓库并配合第 7 条一起做——留着只会让人误以为"管道产物已经不进 Git 了"。

---

## 7. 验收清单（改完之后至少能证明这几点）

1. `find <NAS vault> -name "*.md" | wc -l` == `find <Mac vault> ... | wc -l`，且 `comm -23` 两边都为空。
2. CouchDB 里那 3 个文档 ID 从 404 变成 200，`_changes` 的最新 seq 超过 `67295`。
3. 手机上打开 Obsidian 能看到 09-28 / 09-29 那两篇。
4. 一轮定时任务的日志里出现"发布成功（通道=X）"，而不是 `skipping GitHub push` + 伪 `git_pushed=True`。
5. NAS vault `git status` 干净（或 livesync 模式下容器根本不再执行 git 命令）。
6. 人为把发布通道断开一次，**能在 5 分钟内收到 notify 告警**（这是本次事故最该补上的一条）。

---

## 附：本次实测使用的只读命令

```bash
# NAS 容器与挂载
QNAP_DOCKER=/share/CACHEDEV3_DATA/.qpkg/container-station/usr/bin/.libs/docker
sudo -n "$QNAP_DOCKER" -H unix:///var/run/docker.sock ps -a
sudo -n "$QNAP_DOCKER" -H unix:///var/run/docker.sock inspect zhihu-pipeline --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}'

# NAS vault 的 git 状态（借容器里的 git）
sudo -n "$QNAP_DOCKER" -H unix:///var/run/docker.sock exec zhihu-pipeline git -C /app/notes status -sb

# CouchDB 是否还在接收内容写入（密码只在容器内展开，不落屏）
sudo -n "$QNAP_DOCKER" -H unix:///var/run/docker.sock logs --since 336h obsidian-couchdb \
  | grep -cE "PUT /obsidian-vault/[^_]"
sudo -n "$QNAP_DOCKER" -H unix:///var/run/docker.sock exec obsidian-couchdb sh -c \
  'curl -s -o /dev/null -w "%{http_code}" -u $COUCHDB_USER:$COUCHDB_PASSWORD \
   "http://127.0.0.1:5984/obsidian-vault/_all_docs?limit=1"'
```

---

## 8. 追加发现（2026-10-06 晚）：NAS 上的源码被就地改过，而且已经改坏

对比 NAS `/share/homes/hardass/zhihu-pipeline/src` 与本机源码（逐文件 md5）：

| 文件 | 状态 |
|---|---|
| `bot.py`、`config.py`、`storage.py`、`logging_config.py` | 与本机 `HEAD` 一致 |
| `sync_engine.py` | **与任何提交都不一致**（NAS md5 `75ec86f3…`，本机 HEAD `f163c026…`） |
| `tagger.py` | **与任何提交都不一致**（NAS 停留在"直接覆盖 frontmatter"的旧逻辑，本机已改为"只补空字段、不动已有属性"） |

`sync_engine.py` 的漂移是危险的那种。NAS 上多出这么一段：

```python
class SyncEngine:
    def __init__(self, config):
        self.config = config            # ← __init__ 到此为止

    async def trigger_livesync_sync(self, file_path: str):
        """Trigger an incremental LiveSync via API or Signal file."""
        logger.info(f"Triggering LiveSync incremental sync for: {file_path}")
        # TODO: Implement actual API call or signal file creation
        pass

        # Manifest path: {vault_path}/{collection_dir}/manifest.json   ← 被粘进了这个方法
        self.manifest_dir = os.path.join(...)
        self.manifest = ManifestManager(self.manifest_path)
```

两个后果：

1. **所谓 LiveSync 触发是个空壳**。`trigger_livesync_sync` 里只有 `pass` 和一句日志，却在每篇保存后被调用（NAS 源码第 316 行）。它给了运行日志一层"正在触发 LiveSync"的假象，实际什么都没做——这正是"看起来迁移过了，其实没有"的直接证据。
2. **`SyncEngine.__init__` 不再初始化 `self.manifest`**。manifest 的创建被挪进了那个异步方法里，于是 `self.manifest` 只有在**第一篇文件成功保存之后**才存在。而 `run()` 在读完收藏夹、下载之前就调用 `self.manifest.is_synced()`（第 182 行），且这段不在 per-item 的 `try` 里。

   所以容器重启后的第一轮，只要收藏夹里有可下载的条目，就会 `AttributeError` 冒泡出 `run()`，被 `bot.py:203-204` 的 `except Exception: logger.error(...)` 吞掉、不告警，**一个字节都不会下载**；而进程已经跑起来的实例因为属性已经被"顺手"建好，看起来完全正常。今天能正常输出 `Tagging finished: 0 tagged, 14 failed`，只是因为 18 小时前那一轮恰好保存过文件。

   这不是理论风险：`AGENTS.md` 记录这个容器有过 `Exited (137)`，一次 OOM 重启就足以让它静默停摆。

结论：**NAS 上的代码不是任何一次提交的内容，而是被就地编辑过的版本。** 因此部署前必须先做源码快照备份（迁移方案阶段 2.1），并且要逐文件比对，不能假设"`cp -a` 本机 src 就是纯前进"。本次本机改造已经顺带把这个结构错误纠正回来（`__init__` 恢复初始化 manifest，删除空壳 `trigger_livesync_sync`，改为真正的发布探针）。

对 `tagger.py` 要单独决定：NAS 版本比本机旧，直接用本机版本覆盖会**改变打标签行为**（不再覆盖已有 frontmatter，只在缺字段时补写）。这个改变对本项目是改进，但它会影响存量笔记的标签写法，属于"打标链路"那条工作流，按你的决定现在不动它——所以部署时应**保留 NAS 现有 `tagger.py` 不覆盖**，只同步 `sync_engine.py`、`config.py`、`storage.py`、`bot.py` 和新增的 `publish_probe.py`，避免把两件不相干的事捆在同一次上线里。
