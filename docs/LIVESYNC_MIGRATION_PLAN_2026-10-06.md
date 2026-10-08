# 路线 B 实施方案：NAS 产物经 headless LiveSync 节点投递到 CouchDB

制定日期：2026-10-06（2026-10-06 二次修订，见第 0.1 节）
状态：**计划，尚未执行**。本文所有 NAS / Obsidian / CouchDB 变更都需要逐条授权后再动手。
前置阅读：`docs/SYNC_ANALYSIS_2026-10-06.md`（诊断与实测数据）

## 0.1 已定决策与二次修订（2026-10-06）

已拍板：

- **D1 = 是**：NAS 改用知乎专用目录。
- **D3 = 彻底停用 GitHub**，链路验收通过后不再保留 GitHub 通道。（本方案仅建议在切换完成时做一次最终 push 作为冷快照，那是备份动作，不是通道。）
- **GB10 打标签移出本工作流**：与同步链路无关，现在不修。阶段 1.3 作废，见下方第 0.2 节。
- **阶段 1.1（救 3 篇孤儿笔记）推迟**：链路打通后再一起同步。

本次修订新发现的两个硬约束（都来自上游 `docs/settings.md` 与 CLI README 原文，已核对）：

1. **插件升级不是"建议"，是硬前提**。LiveSync 会记录"内部数据库版本"，原文：**"An older installation cannot dismiss a pause caused by a newer database or settings version."** 也就是说一旦 CLI 节点（1.0.35-cli）把库版本推进，Mac 上的 **1.0.21 会弹出 "Synchronisation paused for compatibility review" 并且自己点不动、无法恢复**，同步会停在旧客户端一侧。所以顺序必须是：**先升级 Mac + 手机插件并与 CLI 同代，再让节点接入正式库**。
2. **`trashInsteadDelete` 已经不是安全机制**。原文：该键"不再显示在设置界面，远程删除改由 Obsidian 自身的 `FileManager.trashFile` 偏好决定"。headless 节点没有回收站这一层，所以旧方案里把它当护栏是错的。真正的护栏只有三条：`syncOnlyRegEx` 白名单、单一写者的知乎专用目录、`.livesync/ignore`。另外 `mirror` 本身**不处理删除**（"If a file is deleted in storage, it will be restored on the next `mirror` run. To delete a file, use the `rm` command"），删除只有在 `daemon` 的 chokidar 事件里才会传播——这一点让"cron 定时 mirror+sync"比"daemon 常驻"更安全，因为它天然不传播误删。

顺带更正与补充：

- Setup URI 现在默认 **Time-bound**（七天固定 UTC 窗口，不是从生成起算七天）。给老设备/CLI 用时要在 **Setup URI availability** 里选 **Compatible (no time limit)**。
- 官方提供了 provisioning wrapper（"runs the exact registry-pinned Commonlib consumer"），建库时会同时初始化 LiveSync 的 database-version 文档——**canary 库要用它建，不要手工 `PUT` 裸库**。
- `mirror` 有前置检查：`isConfigured: true`、`suspendFileWatching: false`、`maxMTimeForReflectEvents: 0`（remediation 模式必须关闭）。

---

## 0. 为什么不用 Obsidian 容器，而用官方 headless CLI

先前判断"NAS 必须跑一个 Obsidian 才能进 CouchDB"在 2026-03 之后不再成立。

LiveSync 作者在 issue #815（"Headless client for syncing CouchDB vault to filesystem"）中确认，官方 headless 实现已经进入 **common library**，并在 2026-03-17 发布了 CLI（仓库路径 `src/apps/cli`，包名 `self-hosted-livesync-cli`，版本线 `1.0.35-cli`）。它复用插件的同一个 core：

```
CLI Main └─ NodeServiceContext └─ LiveSyncBaseCore ─ Node FileSystemAdapter / ServiceModules
```

对本项目最关键的几个能力（README 原文）：

| 能力 | 命令/机制 | 意义 |
|---|---|---|
| 守护模式（默认命令） | `livesync-cli <db> --vault <vault> daemon` | "runs an initial mirror scan and then continuously syncs changes in both directions"，**local filesystem → CouchDB 走 chokidar 文件监听**，管道写进去的文件会被自动推送 |
| 数据库目录与 vault 目录解耦 | `--vault <path>` | 可以把 PouchDB 状态放在 vault 之外，不污染笔记目录 |
| 沿用插件设置格式 | `.livesync/settings.json`（`couchDB_URI/USER/PASSWORD/DBNAME/encrypt/passphrase/isConfigured`） | 直接对接现有 `obsidian-vault` 库，含 E2EE |
| Setup URI | `livesync-cli <db> setup <setupURI>` | 用现有 Mac 客户端导出的 URI 一键导入远端 + 口令 |
| 远端管理 | `lock-remote` / `unlock-remote` / `mark-resolved` / `remote-status` | 新设备首同步会撞到 `Remote database is locked`，CLI 已提供解锁命令，无需在 GUI 里点 |
| 同步范围过滤 | `syncOnlyRegEx` / `syncIgnoreRegEx`（"Patterns apply in both directions"） | **本方案的安全阀**：把节点限制成只看得见知乎子树 |
| 部署形态 | Dockerfile（`node:22-slim` 多阶段，`ENTRYPOINT livesync-cli`）+ systemd 单元与 `install.sh --interval` | QNAP 上可容器化常驻 |

对比另外两种走法：

- `linuxserver/obsidian`（Electron + Selkies 远程桌面）：需要人工首次点选、要求 `--shm-size=1GB`、NAS 当前只有 **1.5GB free / 23GB total**，而且历史上这个容器被 SIGKILL 过（`Exited(137)`）。不选。
- 第三方重写（`obsidian-vault-cli`、Go 版 `obgo-sync` 8–12MB 镜像）：更轻，但作者自述"代码大部分 AI 生成、我只审到一半"。**在生产 vault 上不允许**——这类客户端一旦删除传播逻辑有误，会连带删掉 CouchDB 里的全部笔记和各设备。只用官方实现。

> 待核实项：`self-hosted-livesync-cli` 是否已发布到 npm（本机访问 `registry.npmjs.org` 当前不通，无法确认）；GHCR 上 `vrtmrz/livesync-cli` 镜像不存在（manifest 返回 404），Release 资产只有 `main.js/manifest.json/styles.css`。所以默认按"从源码 `docker build -f src/apps/cli/Dockerfile` 构建"规划，若 npm 可用则简化成 `npx`/全局安装。

---

## 1. 目标拓扑

```
知乎收藏夹 ──Playwright──> NAS 容器 zhihu-pipeline
                                 │ 只写文件，不再 push GitHub、不再 git pull
                                 ▼
                    /share/homes/hardass/zhihu-vault      ← 新建，知乎专用 vault（不含 .git、不含私人笔记）
                                 │ chokidar
                                 ▼
                    NAS 容器 livesync-node（官方 CLI daemon）
                                 │ CouchDB 复制（E2EE，沿用现有口令）
                                 ▼
                    obsidian-couchdb / db: obsidian-vault
                        ├──> MacBook Obsidian（LiveSync 已配置，正常）
                        └──> 手机 Obsidian
```

GitHub 角色（**已定，2026-10-06**）：链路验收通过后**彻底停用**，不再是投递通道。只在切换完成时做最后一次 push 留冷快照，之后不再依赖它。

**核心原则：NAS 上这份 vault 目录必须是"知乎专用、单一写者"。** 现在 `/share/homes/hardass/zhihu-pipeline/notes` 是整库 4.8GB 的克隆（含私人笔记、含 2.6GB 的 `.git`），如果让这个目录接上 LiveSync，节点会把私人笔记一起搬进复制链，并且 chokidar 会盯住 `.git`——而 `.git` 里的 `git checkout`/`reset` 会造成大批文件删除事件，**这些删除会原样传播到 CouchDB 并抹掉 Mac 和手机上的笔记**。这是本方案最大的单点风险，所以必须换目录，不能图省事在原目录上开节点。

---

## 2. 阶段与步骤

### 阶段 1 · 止血（按 2026-10-06 决策裁剪）

- **1.1 救回 3 篇孤儿笔记 —— 推迟**（用户决定：链路打通后再一起同步）。这 3 篇仍只有 NAS 磁盘一份副本，链路验收完成时必须一并核对它们出现在 CouchDB 里。
- **1.2 冻结"删除源"动作 —— 保留，建议立即做**：把 NAS `config.yaml` 的 `sync.remove_after_sync` 临时设为 `false`。在投递通道恢复之前，宁可知乎收藏夹里堆着已下载的条目，也不能再制造单副本数据。这是唯一能立刻消除"不可逆丢失"的开关。
- **1.3 修打标链路 —— 移出本工作流**（用户决定：`https://qwen-coder-next.perilcrosser.com/v1/models` 的 530 与同步无关，暂不处理）。记录一条后果：在修好之前，每轮同步固定 `14 failed`，**新文章的 frontmatter 标签不会写入**，所以链路验收要用"文件是否到达 CouchDB / Mac / 手机"作判据，不要用"标签是否齐全"作判据，否则会误判同步失败。

### 阶段 2 · 单一通道收敛（NAS 目录与 git 解绑）

**进度（2026-10-07 08:05）**：附件命名空间已收敛完成。NAS vault 的 180 个知乎附件目录
（3417 文件 / 816MB）迁到 `assets/知乎附件/`，188 篇笔记重写，2612 个本地链接全部解析成功、
0 失效；提交 `ff40e31` 已推送，git 以 R100 重命名记录（blob 未变）。`assets/` 顶层从 579 降到
400，其余 398 个私人附件目录未被触碰。回滚点：`backups/pre-attachm-20261007-080102`
（正文真拷贝 + 图片硬链接快照，df 用量不变）。
**待办**：Mac 端退出 Obsidian → git pull → 重开 Obsidian 让 LiveSync 一次性追平。

> **2026-10-08 更正**：这一步连同它的白名单方案已**作废**（用户决定「全部回退，不要前缀」）。
> 该库已有 399 篇笔记被移出 `知乎收藏/`，按投放位置划定的前缀跟不上移动，而 `syncOnlyRegEx`
> 双向生效，加了就会漏投。代码已回退到 `assets/<标题>/`，节点不再设路径过滤。
> **已迁移的 180 个目录暂不回退**：回退会在三台同步设备上产生约 3400 次重命名，属高风险churn，
> 需单独确认后再做（回滚点：notes 仓库 `ff40e31`、`9c898c2`；快照 `backups/pre-attachm-20261007-080102`）。
> 真正的缺陷是 `../../assets/...` 这种依赖笔记深度的相对链接（实测深度 1 的笔记有 2 条失效），
> 修法是把嵌入链接改成库内绝对路径，而不是再加一层目录。


- **2.1 备份**（先备份再改，任何一步出问题都能回）：
  - CouchDB：`obsidian-vault` 4.1GB。停写窗口内用 `curl` 触发 `_compact` 前，先整目录 `cp -a /share/homes/hardass/obsidian-livesync/couchdb-data` 到带日期的备份目录（NAS 已有 `backups/` 习惯）。
  - Mac vault：现成的 GitHub 仓库就是备份；再补一份 `rsync` 到本地磁盘。
  - NAS：`/share/homes/hardass/zhihu-pipeline/notes` 整目录快照（含 `.git`）。
- **2.2 建知乎专用 vault**：`/share/homes/hardass/zhihu-vault/`，只放 `知乎收藏/` 与 `assets/`，从现有 NAS 目录 `cp -a` 过来。这一步让 NAS 不再持有任何私人笔记（同时满足 `AGENTS.md` 里"不能误提交 Mac 私人笔记"的顾虑）。
- **2.3 关掉 NAS 的 git**：`git.enabled: false`。这同时消灭 `sync_engine.py:60-63` 的 pull fail-closed 依赖、`git_sync.py:22-51` 的 `ensure_git_repo` 副作用、以及已经成形的工作树脏化（`M manifest.json` + 未跟踪笔记）。
- **2.4 把 `manifest.json` 移出同步子树**：`sync_engine.py:29-30` 目前是 `{vault}/知乎收藏/manifest.json`。它一旦被 LiveSync 双向同步，就可能出现"手机/ Mac 拿到旧 manifest → `is_synced` 判假 → 重复下载或误删已同步文章"。改法：`output.manifest_path` 可配置，默认落到 `{vault}/.pipeline/manifest.json`（点目录，Obsidian 不展示；再配 `syncIgnoreRegEx` 双保险），并保留一次旧路径自动迁移。
- **2.5 拆掉 git 对 `.obsidian` 的托管**（Mac 端 vault 仓库）：`git rm --cached` 掉 `.obsidian/plugins/**/main.js`、`.obsidian/plugins/**/data.json`，加进 vault `.gitignore`。理由不只是仓库 2.27GB：`.obsidian/plugins/obsidian-livesync/main.js` 在 HEAD 里是 **1.0.21**，而 vault 内 `Hermes/2026-09-25 …方案.md` 记录的插件版本是 **1.1.5** 且有 `encrypted.json`（本机现在没有）。这说明 git 通道会**静默回滚插件二进制与配置**——LiveSync 配置被抹平与此高度吻合（推断，非确证）。不先拆掉，阶段 4 的插件升级会被 git 反复打回去。

### 阶段 3 · 部署 headless 节点（进行中，2026-10-07）

**已完成并实测**：CLI 镜像 `zhihu-livesync-node:1.0.35` 构建成功（269MB，官方 `src/apps/cli/Dockerfile`）。Setup URI + 口令导入成功（`isConfigured=true`、`encrypt=true`、profile 活动指向 `obsidian-sync.perilcrosser.com/obsidian-vault`）。白名单已写入：`^知乎收藏/|^assets/知乎视频/|^assets/知乎附件/`，忽略 `/.git/`、`/.obsidian/`、`/.pipeline/`、`manifest.json`，`deviceAndVaultName=zhihu-node`。知乎专用目录 `/share/homes/hardass/zhihu-vault` 已建（正文真拷贝 291 篇、附件与视频硬链接、`df` 净增≈0、`assets/` 顶层只有 2 项）。`mirror` 范围验证通过：**本地库 3711 条 = 291 正文 + 3417 附件 + 2 视频，与磁盘双向差集为 0，私人路径 0、manifest 0**。

> **2026-10-08 更正两处**：（1）白名单已清空，理由见阶段 2 的更正块；（2）节点在 CouchDB 里的真实
> key 是 CLI 生成的 `headless-vault-547ad80b1bc0fb6f`，不是 `zhihu-node`——`deviceAndVaultName`
> 对 CLI 节点不起作用，管道的 `livesync.node_name` 必须填前者，否则心跳检查永远判为"节点不新鲜"。

**方案调整**：原计划的独立 canary 库被换成**单文档定向探针**（`知乎收藏/_node_probe.md`，带唯一标记串）。原因：独立库测不到真正的风险——CLI 写入的文档能否被插件端解密；而正式库里知乎文档已存在（Mac 自己推的），所以探针必须用一个**库里尚不存在的新文档 ID**，失败时可随手删除、不影响既有数据。实测锁库期间探针被正确拒绝（远端 `not_found`），说明未接受节点无法污染库。

**当前状态**：库已解锁（用户 GUI 操作），节点首次 `sync` 进行中。注意解锁把 `accepted_nodes` 从 3 个重置成 1 个，所以**收尾必须先让其它设备重新入列再加锁**，否则会静默挡住那台仍在正常同步的 1.0.21 设备。

**尚未做的收尾项**（节点验通后）：把节点容器化常驻（**`daemon` 模式**，`--vault` 挂整库 `notes` 目录，`user: root`，`mem_limit` 显式设置）、`git.sync_mode` 翻到 `livesync`、并在 NAS 配置里补 `livesync:` 段（`couchdb_url` 用本机 `http://127.0.0.1:5984` 而不是公网隧道、`db_name: obsidian-vault`、**`node_name` 必须填 CLI 生成的 `headless-vault-547ad80b1bc0fb6f`**，`deviceAndVaultName=zhihu-node` 对 CLI 节点不起作用，填错会让心跳永远判为不新鲜）；再做一次"停节点必须在 5 分钟内告警"的故障演练，然后按决定彻底停用 GitHub，并删除 `zhihu-vault`（808M）与 `.git`（2.6G）。

> 阶段 2.2 的"知乎专用 vault"路线已被上面的拓扑决定取代：既然不设路径过滤，节点挂哪儿就把整库落到哪儿，另建一个专用目录只会多出一份副本并让被移走的笔记落在过滤之外。

### 阶段 3b · 原金丝雀方案（已被上面的探针方案取代，保留作参考）


- **3.1 版本对齐（前置硬条件，不可跳过）**：这不是建议，是顺序约束。
  - 现网实测：Mac 插件 **1.0.21**（`main.js` 与 git HEAD 一致），上游最新 **1.0.35**（2026-10-05 发布），CLI **1.0.35-cli**，`_local/obsidian_livesync_sync_parameters` 为 `protocolVersion: 2`，`_local/obsydian_livesync_milestone` 为 `locked: true` + `accepted_nodes` 3 个。
  - 依据 `docs/settings.md`：**旧安装无法解除由更新版本造成的同步暂停**（"An older installation cannot dismiss a pause caused by a newer database or settings version"）。所以**必须先把 Mac 与手机升级到与节点同代**，否则节点一连正式库，Mac/手机就会卡在 "Synchronisation paused for compatibility review" 上且自己点不动——那会把唯一还活着的通道也弄停。
  - 这一步依赖阶段 2.5 先完成，否则升级后的 `main.js` / `data.json` 会被 git pull 打回 1.0.21。
  - 需要你在 Obsidian 里操作（我没法代劳）：升级插件 → `Self-hosted LiveSync: Copy settings as a new Setup URI` → **在 Setup URI availability 选 "Compatible (no time limit)"**（默认 Time-bound 是七天固定 UTC 窗口，剩余时间可能远小于七天）→ 设一个独立 URI 口令 → 把 URI 写入 NAS `/share/homes/hardass/livesync-node/setup.uri`（`chmod 600`）。**URI 与口令不要走同一渠道，不要贴进对话，不要提交进 Git。** CLI 侧用法：`printf '%s\n' "$SETUP_PASSPHRASE" | livesync-cli <db> --settings <file> setup "$SETUP_URI"`。
- **3.2 构建/安装**：优先官方 provisioning wrapper（`docs/setup_own_server.md` 里的 "registry-pinned Commonlib consumer"，建库时同时初始化 LiveSync 的 database-version 文档）。`self-hosted-livesync-cli` 是否已发 npm 待核实（本机到 `registry.npmjs.org` 当前不通），GHCR 无预构建镜像（manifest 返回 404），Release 资产只有插件三件套 → 默认按"NAS 上 `git clone --depth 1` + `docker build -f src/apps/cli/Dockerfile -t livesync-cli .`"规划（基础镜像 `node:22-slim`，`ENTRYPOINT livesync-cli`，`/data` 为数据库目录，可用 `LIVESYNC_DB_PATH` 覆盖）。
- **3.3 金丝雀库**：用 3.2 的 wrapper 新建 `obsidian-zhihu-canary`，节点先接这个库，**不碰 `obsidian-vault`**。四项验收：
  1. 往 zhihu-vault 新建/修改一个测试文件，30 秒内 canary 库出现对应文档（`_all_docs` 前缀命中），升级后的 Mac 能看到。
  2. **跨版本共存探针**：canary 库同时接"升级后的 Mac 插件"和"CLI 节点"，双向各写一次，确认两边都不触发 paused-for-review、不产生 conflict 文档。这一步就是 3.1 那条硬约束的实验验证，通过了才允许碰正式库。
  3. 关键否定测试：**节点启动时不得删除任何已有文档**（比对 `doc_count` 与 `_all_docs` key 集合），并确认 `.git`、`.pipeline/manifest.json`、非知乎目录都不在范围内。
  4. 注意 `mirror` 会把"库里存在、磁盘缺失"的知乎文档**反向落盘到 NAS**（UPDATE STORAGE 分支）——这是预期行为，磁盘文件数会变多，别当异常。反过来说，不加 `syncOnlyRegEx` 时它会试图把库里 1399 篇笔记和 4.8GB 附件全量写进 NAS，这也是白名单必须先在 canary 验的原因。
- **3.4 正式接入 `obsidian-vault`**：`.livesync/settings.json` 显式收窄范围。护栏是 `syncOnlyRegEx` 白名单 + 单一写者目录 + `.livesync/ignore` 三条；**`trashInsteadDelete` 已从护栏中移除**——上游 `docs/settings.md` 明确它是遗留键，远程删除实际由 Obsidian 自身的 `FileManager.trashFile` 偏好决定，headless 侧没有回收站保护层。
  - 需要的键值（`couchDB_URI/USER/PASSWORD/passphrase` 由 setup URI 导入，不手抄）：
    - `liveSync: true`、`encrypt: true`、`isConfigured: true`
    - `syncOnlyRegEx`: `^知乎收藏/|^assets/知乎视频/`
    - `syncIgnoreRegEx`: 排除 `/.git/`、`/.pipeline/`、`/.obsidian/`、`manifest.json`
    - `suspendFileWatching: false`、`maxMTimeForReflectEvents: 0`（`mirror` 的三项前置检查之二，remediation 模式必须关闭）
  - 再放一份 `.livesync/ignore`（minimatch 语法、双向生效、支持 `import: .gitignore`）作纵深防御；**改这个文件要重启节点才生效**。
  - 首次接入依次 `remote-status` → 若 locked 则 `mark-resolved` / `unlock-remote`（issue #815 里提到的"首同步被 lock 卡住、只能手工往 `accepted_nodes` 塞 nodeid"的老问题，CLI 已提供命令）；`accepted_nodes` 会多出 `zhihu-node`，需要你在 Mac 的 Obsidian 里确认接受新节点。
- **3.5 常驻形态：先 cron，后 daemon**。起步用定时任务跑 `mirror` + `sync` 一轮，理由是 **`mirror` 天然不传播删除**（上游原文：文件在存储端被删除，下次 `mirror` 会把它从库里恢复回来；要真删必须显式用 `rm`）。也就是说 cron 形态下，误删文件、误改目录都不会污染 CouchDB，行为可预测、易回滚。稳定跑满一周、且确认不需要"实时"之后，再考虑 `daemon`——daemon 走 chokidar，**创建/修改/删除都会推给 CouchDB**，届时删除才成为真实风险，必须先确保 `syncOnlyRegEx` 白名单与"单一写者目录"无可绕过。daemon 若启用，建议 `--interval 60` 轮询而非 `_changes` 长连接（CouchDB 走 Cloudflare Tunnel，长连接稳定性未知，README 也正是这样建议的）。容器显式设 `mem_limit`（NAS 当前仅约 1.5GB free，且同类容器历史上有 `Exited(137)`）。
- **3.6 视频体积边界重算**：`couchdb-etc/local.ini` 里 `max_document_size = 50000000`，`videos.py` 的 50MB 是十进制同值。`docs/VIDEO_SYNC_TOPOLOGY_2026-09-20.md` 已记录 49.15MiB（≈51.5M 字节）被 LiveSync 拒收的案例。接入后拿一个 40–50MB 边界视频做真实入库探针；`sync.video_max_size_mb` 按探针结果下调，并且**超限时要让整轮发布判为失败**，不能"下载成功但永不同步"。

### 阶段 4 · 代码改造（让模式真的有语义）

**状态：2026-10-06 已在本机工作树完成并通过测试（83 passed），尚未部署到 NAS。** 下表保留为改动索引。

| 优先级 | 位置 | 改动 |
|---|---|---|
| P0 | `sync_engine.py:382-399` | 去掉 `git_pushed = True` 的伪成功。返回中性的 `published: bool` + `publish_channel` + `published_paths`（本次新写入的相对路径列表） |
| P0 | `sync_engine.py:323-335` | `remove_after_sync` 加安全阀：**只有发布确认后**才从知乎收藏夹移除。livesync 模式下"发布确认"来自阶段 4 最后一条的节点探针 |
| P0 | `bot.py:133-148`、`bot.py:179-188` | 通知文案按通道生成，不再写死"已自动推送到配置的 GitHub 仓库"；未发布必须显式告警 |
| P0 | `bot.py:203-204` | 调度循环异常走 `notify`，不再只写日志 |
| P1 | `sync_engine.py:60-63`、`git_sync.py:22-51,59` | `sync_mode != "git"` 时完全跳过 `git_pull` / `ensure_git_repo`（不再有 `git init`、`remote set-url`、`branch -M`、全局 `safe.directory` 副作用） |
| P1 | 新增 `src/zhihu_pipeline/publish_probe.py` | **发布确认探针**：读 CouchDB（远端 + 凭据走环境变量），确认本批 `published_paths` 对应文档 ID 已存在（HTTP 200），并检查 `accepted_nodes` 里节点心跳新鲜（如 15 分钟内）。失败则本轮判"未发布"并告警。这一步也顺带解决"这次断裂拖了两天没人知道" |
| P1 | `config.py:96-104, 189-198`、`config.example.yaml` | `sync_mode` 取值改为 `git` / `livesync_node` / `none` 并加 `GIT_SYNC_MODE` 环境变量覆盖；示例配置补齐该字段 |
| P1 | `config.yaml`（NAS + Mac） | `repo_url` 内嵌 GitHub token 改为 `GIT_REPO_URL` 环境变量，并轮换 token（`config.py:193` 本来就支持） |
| P1 | 代码仓库 `.gitignore` | 删掉 `知乎收藏/`、`assets/知乎视频/` 那两行，或移到 vault 仓库并配合阶段 2.5——留着只会误导"产物已经不进 Git" |
| P2 | `tests/test_git_sync.py` + 新增 `tests/test_publish.py` | 覆盖：`livesync_node` 不发任何 git 网络请求、`published=False`、探针失败会触发 notify、超阈值体积视频判失败 |
| P2 | `README.md`（第 3、20、34、38、83 行附近）、`docs/DESIGN.md`、`docs/CHANGELOG.md`、`docs/NAS_AUDIT_2026-09-16.md` | 重写"同步拓扑"章节；补这次迁移的变更记录；NAS 审计里 GB10 与 GitHub 的验收结论标注为已过期 |

### 阶段 5 · 验收（全部要用实测证据，不看日志措辞）

0. **判据口径**：GB10 打标当前是断的（530，每轮固定 `14 failed`），且已移出本工作流。所以阶段 5 全部以"文件是否到达 CouchDB / Mac / 手机"为判据，**不要**用"frontmatter 标签是否齐全"判断同步成败，否则会把两件不相干的事混成一个假故障。
1. 在知乎收藏夹点一篇**新**文章，等一轮定时同步：Mac 与手机都能在 5 分钟内打开它，且图片正常显示。
2. CouchDB：`obsidian-vault` 的 `_changes` 最新 seq **超过 67295**（这是当前停滞点），`_all_docs` 里能查到阶段 1 那 3 篇（不再是 404）。
3. `find /share/homes/hardass/zhihu-vault -name "*.md" | wc -l` 与 Mac 侧知乎笔记数一致。
4. NAS 容器日志里不再出现 `skipping GitHub push` 与伪 `git_pushed=True`；通知文案写明"经 LiveSync 节点发布"。
5. NAS 上私人笔记不再存在（`zhihu-vault` 里没有非知乎目录），`git` 相关命令不再在该目录执行。
6. **故障演练**（最重要，直接检验这次事故的根因是否被修掉）：手动停 `livesync-node` 容器，等一轮定时同步，应在 5 分钟内收到 notify 告警，且这一轮**不会**从知乎收藏夹移除条目（阶段 4 第 2 条的安全阀生效）。

---

## 3. 风险清单

| 风险 | 后果 | 缓解 |
|---|---|---|
| 节点看见 `.git` 或整库 → `git checkout` 造成批量"删除"事件传播 | **多设备笔记被抹**（最高危） | 阶段 2.2 换知乎专用目录 + 2.3 关 NAS git；阶段 3.4 用 `syncOnlyRegEx` 白名单 + `.livesync/ignore`；起步用 cron（`mirror` 不传播删除）；金丝雀库先跑 |
| 官方 CLI 仍较新（2026-03-17 才发布，README 自述 "about a week, never thought it was quite perfect"） | 边界 bug、首同步被 locked milestone 卡住 | 阶段 3.3 金丝雀 + 阶段 2.1 全量备份；先 cron（`mirror`+`sync`）跑满一周再考虑 daemon；CLI 已提供 `remote-status`/`mark-resolved`/`unlock-remote` 处理锁库 |
| 三端版本不一致（Mac **1.0.21** / 上游 1.0.35 / CLI 1.0.35-cli） | **旧客户端会卡在 "Synchronisation paused for compatibility review" 且无法自行解除**（上游明文：older installation cannot dismiss a pause caused by a newer database version）——等于把唯一活着的 Mac↔手机通道也弄停 | 顺序硬性要求：阶段 2.5（git 摘出 `.obsidian`）→ 升级 Mac+手机 → canary 库做跨版本共存探针 → 才允许节点接正式库 |
| `daemon` 的 chokidar 会传播删除事件 | 节点侧误删 → CouchDB → 全设备丢笔记 | 起步用 cron `mirror`+`sync`（`mirror` 不传播删除，缺文件反而从库里恢复）；`syncOnlyRegEx` 白名单 + 知乎专用单一写者目录 + `.livesync/ignore`；**不要依赖 `trashInsteadDelete`，它是遗留键** |
| Cloudflare Tunnel 长连接不稳 | 实时复制退化 | 用 `--interval 60` 轮询模式（README 明确建议此场景） |
| NAS 内存只有 ~1.5GB free（曾出现容器 `Exited(137)`） | 节点被 OOM，静默停止投递 | 显式 `mem_limit` + 阶段 5 第 6 条故障演练必须能告警；节点与 pipeline 分离，pipeline 挂掉不影响已下载数据 |
| 视频超过 CouchDB 文档上限 | "下载成功但永不同步"的静默丢失 | 阶段 3.6 探针实测后下调上限，并在代码里让它判失败 |
| 私人笔记从此不经任何通道到 NAS | Hermes 类 agent 若依赖 NAS 上那份整库会读不到 | 迁移前确认 Hermes 的读取路径。若它依赖整库镜像，应让它直接连 CouchDB（只读）或改走 Mac 本地路径，而不是继续往 NAS 拷私人笔记 |

---

## 4. 需要准备的凭据与输入（不要贴进对话）

1. LiveSync **Setup URI**（含 CouchDB 凭据 + E2EE 口令）：从 Mac Obsidian 的 LiveSync 设置里 "Copy setup URI"，写入 NAS `/share/homes/hardass/livesync-node/setup.uri` 并 `chmod 600`。
2. `obsidian_admin` 的 CouchDB 口令（已在 `obsidian-livesync/docker-compose.yml` 里），仅用于阶段 5 的只读验收探针。
3. GitHub token 轮换后的新值 → 只进 `GIT_REPO_URL` 环境变量。（当前 NAS 与本机 `config.yaml` 的 `repo_url` 里内嵌了明文 token，建议无论走哪条路线都轮换掉。）

---

## 5. 明确不做

- 不写自研 CouchDB 客户端，不让 Python 直接拼 LiveSync 私有文档格式（chunk 编码、`h:` 索引、minified、E2EE 都随版本变，等于自己养一个高不兼容风险的 fork）。
- 不在生产 `obsidian-vault` 上试错；所有新行为先在 canary 库验过。
- 不在未确认前动 NAS 容器（不启动/重建/删除既有容器，`AGENTS.md` 约束）。
- 不引入第三方重写版 headless 客户端。

---

## 6. 决策状态

已定（2026-10-06）：

- **D1 = 是**：NAS 改用知乎专用目录 `/share/homes/hardass/zhihu-vault`。
- **D3 = 彻底停用 GitHub**（链路验收通过后）。仅建议在切换完成时做一次最终 push 当冷快照备份，那不是通道。
- **GB10 打标签 = 移出本工作流**，与同步无关，暂不修。
- **3 篇孤儿笔记 = 推迟**到链路打通后一并处理。

仍待你确认：

- **D2′（升级为 D2 的更正，阶段 3.1）**：Obsidian 插件升级**不属于"要不要一起做"的选项，而是硬前提**。上游明文规定旧安装无法解除由新版本造成的同步暂停，你 Mac 现在是 1.0.21。不先升级 Mac + 手机就接节点，会把 Mac↔手机这条唯一活着的通道也弄停。升级要在 Obsidian 界面里点，我没法代劳；顺序上还必须排在阶段 2.5（把 `.obsidian` 从 git 摘出来）之后，否则升级会被 git 打回。
- **D4（阶段 3.5，建议已加强）**：起步用 **cron 定时 `mirror` + `sync`**，稳定一周再评估 `daemon`。新增依据：`mirror` 天然不传播删除（缺文件会从库里恢复回来，真删要显式 `rm`），而 `daemon` 的 chokidar 会把删除推到 CouchDB。在你这种"节点只挂一个子树"的部分镜像拓扑下，cron 形态的误操作后果小得多。
- **D5（授权范围）**：默认我只在 Mac 工作树改代码与文档；NAS 上的目录搬迁、配置改动、新容器、CouchDB 建库，每一步单独找你确认；`AGENTS.md` 的"默认不启动/重建/修改 NAS 容器"约束优先。

建议的执行节奏：**阶段 1.2（关 `remove_after_sync`）→ 阶段 2 备份 + 建知乎目录 + 关 NAS git + 拆 `.obsidian` 出 git → 你在 Mac/手机升级插件 → 阶段 3.2/3.3 建 canary 并跑通四项验收 → 阶段 3.4 接正式库 → 阶段 4 代码改造部署 → 阶段 5 验收含故障演练 → 做最后一次 GitHub push 后停用**。其中阶段 4 的代码改造可以在 canary 验证期间并行在 Mac 工作树完成与测试，不占用链路。
