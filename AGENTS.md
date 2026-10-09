# ZhihuPipeline Agent 运行规则

## NAS / QNAP Docker

这个项目的生产容器运行在 QNAP NAS，不运行在当前 Mac 的本地 Docker 中。

- NAS：`hardass@192.168.1.195`；Tailscale fallback：`hardass@100.75.232.63` 或 `hardass@nas8c117a`。
- **2026-10-07 实测**：LAN 地址 `192.168.1.195` 能 ping 通但 22 端口拒绝连接，而 `nas8c117a`（MagicDNS）22 端口正常。**优先用 `hardass@nas8c117a`**；不要因为 LAN 端口 refused 就判定 NAS 关机或不可达。
- SSH 使用本机 `~/.ssh/id_ed25519`，优先 `BatchMode=yes`、`IdentitiesOnly=yes`，不要使用 `ssh -A`。
- QNAP 普通 Docker 客户端不在 PATH：`/share/CACHEDEV3_DATA/.qpkg/container-station/usr/bin/.libs/docker`
- Zhihu Pipeline 使用普通 Docker socket：`/var/run/docker.sock`；不要误查 `system-docker.sock`。
- 远端 Compose 工作目录：`/share/homes/hardass/zhihu-pipeline`。

推荐的只读检查方式：

```bash
QNAP_DOCKER=/share/CACHEDEV3_DATA/.qpkg/container-station/usr/bin/.libs/docker
sudo -n "$QNAP_DOCKER" -H unix:///var/run/docker.sock ps -a
sudo -n "$QNAP_DOCKER" -H unix:///var/run/docker.sock inspect zhihu-pipeline
sudo -n "$QNAP_DOCKER" -H unix:///var/run/docker.sock logs --tail 200 zhihu-pipeline
```

先检查再修改：不要因为 Container Station 显示 `Others` 就启动或重建容器；以 Docker `inspect` 的真实状态、退出码和事件为准。默认不启动、删除、重建或修改 NAS 容器。

## 数据安全

- NAS 容器挂载了 Obsidian notes、源码、配置和日志；操作前先确认挂载范围。
- 只要 `git.sync_mode` 还是 `git`，NAS pipeline 就只能提交知乎收藏目录，不能使用全库 `git add -A`，否则可能提交 Mac 上的私人笔记和 `.obsidian` 设置。
- **`~/notes` 里绝不允许 `git clean` / `reset --hard` / 覆盖式检出**：插件本体、`personal assets/`、`OneNote/`、大量附件只存在于 LiveSync 而不在 git 里，2026-10-07 已经这样丢过一批文件。
- manifest 损坏、Git pull 失败或外部模型不可用时必须 fail closed；不能把损坏 manifest 当空数据继续运行。
- 不读取或输出 Telegram token、GitHub 凭据、模型 API key、LiveSync 口令；健康检查只报告脱敏后的状态码和错误类型。查 `livesync-settings.json` 时只打印键名与长度，不打印值。

## 当前已知运行状态（2026-10-09）

历史叙事与事故经过见 `docs/CHANGELOG.md`，实测清单与遗留缺陷见 `docs/AUDIT_2026-10-09.md`。这里只留会影响下一步动作的事实。

- **投递通道 = LiveSync，已实测通过。** NAS `config.yaml` 的 `git.sync_mode: "livesync"`，GitHub 完全不参与（连 pull 都不做）。`git` 模式只作为回滚路径保留，正式停用后要连同 `git_sync.py` 一起删。管道自己不写 CouchDB，只用 `publish_probe` 核对文档是否到达，核对不到就不消费知乎收件箱。
- **节点拓扑已定稿**：`zhihu-livesync-node` 挂整库目录（管道写的那个 `notes`）、`daemon` 模式、无路径白名单、`user: root`。**删除语义是"中性"的**（实测：NAS 侧删文件不推给服务器，服务器侧删文档也不会被推回来复活）——不要指望它传播删除，也别担心它误删库。
- **CLI 复制能力上限（第 4 次实测确认，别再试）**：`livesync-cli` 在命令行模式下不把复制结果写进自己的存储（日志原话 "Replication result received, but not processed automatically in CLI mode"）。全新空存储 + 已清理重复文档的库跑 `sync`，2 分钟只落 6.1MB。生产节点那 2.8GB 存储是本地文件 push 出来的，不是拉下来的。推论：`pull`/`cat`/`info`/`mirror` 只能服务它已有的文档；**"节点库里没有"不等于"文件不存在"**。
- **69 个"服务器独有文件"是误判，已结案（2026-10-09 二次核对）**：那 78 个文档（69 真实内容 + 9 探针/垃圾）全部带 LiveSync 的软删除标记（文档体 `deleted: true`，不是 CouchDB tombstone）。它们的最后一次变更在 changes feed 里位于 4 万条之前（早于约 2026-09-22），且 10-09 早晨的整库备份里就已经是删除态——所以既不是 10-07 事故、也不是重复文档清理、也不是重开 Mac 同步造成的，而是**数周前在某台设备上被删除、删除正常同步**的旧账。分块还在（等 GC），按单个文件复活仍可行（写 `deleted: false` + 原 mtime 的新修订）；**在确认不再需要复活之前，任何设备上都不要点 Maintenance 里的 Perform Garbage Collection**。NAS 磁盘上只剩其中 1 个（节点删除中性的副产品）。
- **用户决定不加锁（2026-10-09）**：历史上加过一次又解开，解开那一下把 `accepted_nodes` 重置成只剩一台，是后来"名单不齐"麻烦的来源。现在的取舍是明确的：连接仍需服务器地址 + CouchDB 凭据 + E2EE 口令三样齐备，锁只额外防"凭据已泄漏的旧设备"这一种场景，用户接受不防。`locked` 保持 `false`，**不要再提议加锁**，也不要再为加锁去补齐 `accepted_nodes`。
- **键规则**：文档 `_id` 是小写化的 vault 相对路径，原始大小写在 `metadata.path`；QNAP 的 `/share/homes` 区分大小写，Mac 的 APFS 不区分。写盘必须用 `metadata.path`。`doc_count` 不是文件数（含 `h:+` 分块与删除记录）。
- **节点忽略名单必须含 `/\.obsidian/`（任意深度）与 `\.DS_Store$`**：库里有嵌套的 Obsidian 子库（`Jobs/`、`Travel/Japan202606/`、`Investiment/`），各带自己的插件与 `github-sync.log`，不过滤会整批进库且日志每轮增长。
- **2026-10-09 已做的清理**：NAS 打标关掉（`tagger.enabled: false`，端点长期 530，18 条会无限重试）；删除弯路残留 `restore-vault`/`fetchlocal`/`node-data.broken-*`/`~/zhihu-vault` 与两个死容器；仓库删掉 `src.tar.gz`、`IMPLEMENTATION_PLAN.md`、一次性脚本、LM Studio 本地打标残留，六份时点文档合并进本文件与 `docs/AUDIT_2026-10-09.md`。
- **保留的回退点**：CouchDB `obsidian-vault-backup-20261008`（清理重复文档前的整库副本，`doc_count` 66603）、Mac `~/notes-snapshot-20261008`（6778 文件 / 2.5G）、NAS `backups/pre-attachm-20261007-080102`（2.5G）与 `backups/pre-knowledge-merge-20260917-220517`（5.1G）、`livesync-node/livesync-settings.pre-daemon.*`、`keyvalue-db.stale-provenance.*`。
- **遗留待办**：观察 1–2 天投递正常后停用 GitHub（删 `git_sync.py` 与 `sync_mode` 的 git 分支、清 `auto_archive`/`clear-archive`/`cleaner.py` 半退役代码、移走 `notes/.git` 2.6G）；修 `images.py` 的深度依赖相对链接 `../../assets/...`（笔记移到一级目录会断链，实测已有 2 处）；无视频的 pin 目前被静默跳过；GB10 打标端点仍坏（独立工作线）。69 个"缺失文件"已结案为旧删除，插件本体已重装，加锁已取消——这三项不要再出现在待办里。
- **Mac 的上传方向是坏的（2026-10-09 晚反复实测，未修好）**：拉取完全正常（全量重建后服务器 6,722 条活文档在 Mac 上一份不缺、零缺失），但**新增文件永远推不上去**：22:09 放一个探针文件，5 分钟后服务器仍无记录；连续模式、一次性模式、以及 20:13 那次把 IndexedDB 整个轮转重建之后，结果一样。隧道写入用 curl 从 Mac 直测可达源站（401），所以不是网络层；重启后插件的离线扫描报 "6721 completed / 3507 skipped / 0 failed" 却一条新文档都没发，说明它把本地新增判定成"无需上传"。下一个候选修法：在 Mac 上重新导入一次 Setup URI 刷新连接凭据（不要再靠重建索引，那条路已实测无效）。在那之前，Mac 上新写的笔记要经 NAS 代发（拷进 NAS vault，节点秒级发布）。

- **附件布局已统一（2026-10-09 晚，用户授权）**：`assets/知乎附件/<标题>/` 全部并回 `assets/<标题>/`，189 篇笔记的 2,618 条链接在 NAS 与 Mac 两侧都改过，抽样 561 条链接零断链；服务器端 3,426 条旧前缀文档已核对新路径逐字节同源后打 tombstone，新路径文档由"复制文档体、改 `_id`/`path`、复用同一批 `h:+` 分块"直接建出来（分块是内容寻址的，不需要口令或重新上传）。NAS 改前的 189 篇笔记备份在 `notes/.pipeline/backups/pre-unify-20261009-211337`（节点忽略 `.pipeline/`，不会同步）。
- **节点不会为"搬家"重发文件（实测教训）**：把文件从一个路径移到另一个路径后，节点本地存储里仍留着旧路径的同步记录，重启后的 mirror scan 也不会为新路径补发文档；同理，改过正文的笔记要等节点扫完才会推（扫描期间 CPU 100%+，可能十几分钟）。给附件改路径时，正确做法是服务端直接复制文档体建新路径文档（本次做法），或者先清掉节点本地存储再让它重建。


## NAS 运维陷阱（实测）

- **单文件 bind mount 只认 inode。** 用编辑器保存或 `sed -i` 改 `config.yaml` 会替换 inode，容器内读到的仍是旧内容，必须重启容器才会重新解析绑定路径。原地写入请用 `cat new > old`，或改完固定重启一次。
- 判断容器是否真的加载了新代码：`src` 是 live mount，但 Python 进程只在启动时导入，**改完源码同样需要重启容器**；`docker exec ... python -m zhihu_pipeline status` 可以另起进程验证新代码。
- 镜像的 `ENTRYPOINT` 已经是 `python -m zhihu_pipeline`，`docker run` 只传子命令。写成 `docker run ... python -m zhihu_pipeline <cmd>` 会执行 `python` 这个不存在的子命令，而 Click 的错误照样打到 stdout/stderr，容易误读成"没有输出=成功"；看原始输出，不要只看退出码。
- `docker exec` 转发 stdin 必须加 `-i`，否则需要口令的 CLI（如 livesync `setup`）会静默读到空输入。
- NAS 上没有 `python3`、`comm`、`timeout`、`grep -P`（BusyBox 环境）；跨端脚本要用 POSIX 工具，或把逻辑放进容器内执行。
- 校验 git 脏路径必须用 `git status --porcelain -z`：路径含空格时 git 一律加引号，`core.quotepath=off` 管不了这个，按行解析会误判成越界改动。
- **摘除 git 跟踪 ≠ 删除文件，但紧随其后的检出/清理会。** `git rm --cached` 只动索引；可是文件一旦变成"未跟踪"，之后的 `git checkout .`、`git restore`、`git clean -fd` 就会把它们当作垃圾清掉。vault 这种仓库里，插件本体、`personal assets/`、`onenote/`、大量附件都只存在于 LiveSync 而不在 git 里，一旦误清就是几千个文件。摘除跟踪之后**立刻**用 `git status --porcelain -z --untracked-files=all` 确认未跟踪文件仍然在磁盘上，并且在此之前先做带日期的目录备份。
- **节点上传之前快照必须自洽。** 手工拼出来的 `zhihu-vault` 有笔记引用了没被拷进去的附件目录，节点把坏笔记推上线、覆盖掉各设备本地的好版本，实测在 Mac 弹出 8 个冲突对话框、手机端出现 "This file has unresolved conflicts"。加白名单不能防这个（问题在内容不在路径），所以重建快照后必须先核对"笔记引用的附件目录都存在"再启动节点；重建一律用管道自己的输出目录，不要手拼。
