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
- NAS pipeline 只能提交知乎收藏目录，不能使用全库 `git add -A`，否则可能提交 Mac 上的私人笔记和 `.obsidian` 设置。
- manifest 损坏、Git pull 失败或外部模型不可用时必须 fail closed；不能把损坏 manifest 当空数据继续运行。
- 不读取或输出 Telegram token、GitHub 凭据、模型 API key；健康检查只报告脱敏后的状态码和错误类型。

## 当前已知运行状态（2026-09-16，历史快照）

- `zhihu-pipeline` 已按用户授权重新启动；当前 Docker 状态为 `running`，重启次数为 0，`OOMKilled=false`。
- 生产容器已切换为 `worker` 入口，`telegram.enabled=false`，不再启动 Telegram polling；Telegram token 不再是生产主流程依赖。
- `notify-gateway` 保持启用，负责知乎登录失效时的二维码、登录成功和失败告警通知；其健康检查返回 HTTP 200。
- GB10 Qwen3-Coder-Next 已由 `gb10-coder-next-vllm.service` 托管，监听本机 `127.0.0.1:8002`；Cloudflare 专用 Tunnel active/enabled，公网 `/v1/models` 和最小聊天探针均返回 HTTP 200。
- Mac 工作树中验证过的 manifest 防护、原子写入、Git pull fail-closed、范围化 push、tagger timeout 和 Telegram 降级修复已部署到 NAS。
- NAS 配置中的 GitHub remote 已恢复有效认证；容器内 `git pull --rebase origin main` 已成功，工作树干净，当前相对 `origin/main` 为 `ahead 2`。
- 尚未主动触发完整知乎下载/打标签批处理，避免在验收前制造新的文章或提交；已完成 Git pull、GB10 tagger 预检和 worker 启动验收。


## 当前已知运行状态（2026-10-07）

- 投递通道：NAS `zhihu-pipeline` 容器 `running`、`restarts=0`，`git.sync_mode: "git"`，GitHub 投递已恢复正常。
- LiveSync 节点：镜像 `zhihu-livesync-node:1.0.35` 已构建并可用；`livesync-node/node-data/livesync-settings.json` 已导入 Setup URI（`isConfigured=true`、`encrypt=true`，库地址 `obsidian-sync.perilcrosser.com/obsidian-vault`）。**路径白名单已清空**（原因见下条）。节点在 CouchDB 里的 key 是 CLI 生成的 `headless-vault-547ad80b1bc0fb6f`，`deviceAndVaultName=zhihu-node` 对 CLI 节点不起作用。
- **附件专用前缀方案已作废（2026-10-08，用户决定）**：曾把 180 个知乎附件目录迁到 `assets/知乎附件/`（notes 仓库提交 `ff40e31`、`9c898c2`）以便 LiveSync 按路径过滤。但实测发现该库已有 **399 篇笔记被用户移出 `知乎收藏/`**（散在 `AI/Agent/`、`Life/`、`Business/` 等），任何以 collection 目录为界的方案都跟不上这种用法；而 LiveSync 的 `syncOnlyRegEx` 双向生效，不过滤只是让节点看不到范围约束，加过滤则会漏投被移走的笔记。因此**代码已回退到 `assets/<标题>/`，节点不再设路径过滤**。
- **已迁移的数据暂不回退**：那 180 个目录当前自洽可用（笔记引用与文件位置一致，Mac 与手机图片均正常）。回退数据会在三台同步设备上再制造一轮 3400 文件改名，换不到任何功能修复。要回退需用户单独确认。
- **真正待修的缺陷**：`images.py` 写的是深度依赖的相对链接 `../../assets/...`，笔记被移到一级目录（如 `Life/x.md`）时链接会跳出库根而失效（实测已有 2 处断链，移到二级目录的 66 处正常）。修法是把嵌入链接改成 Obsidian 可解析的库内路径，与目录前缀无关。
- **未完成且需人工接手的第一件事**：CouchDB 目前处于 `locked:false`，且那次开关把 `accepted_nodes` 重置成只剩 MacBook 一个节点。恢复流程见 `deploy/livesync-node/README.md`「首次接入的顺序与两个实测坑」：先让其它设备各打开一次 Obsidian 重新入列，名单齐全后再加锁，最后验证锁着仍能同步。**不要在名单只有 1 个节点时加锁**，那会静默挡住仍在正常同步的设备。
- **已决定：节点挂整库目录、用 `daemon` 常驻（2026-10-08 定稿）。** `sync` 本来就是整库复制，`syncOnlyRegEx` 只约束哪些*文件*参与同步、不约束复制范围，所以节点无论如何都会持有约 4.3GB 的全库副本（并为加解密持有口令）。这不是需要防的风险：LiveSync 的 E2EE 针对的是不可信存储与传输环节（托管库、被截获的隧道、丢失的手机），而不是用户自己的 NAS；相反 NAS 上原本就放着 5.1GB 明文整库加 2.6GB `.git`，暴露程度更高。据此：`LIVESYNC_VAULT_DIR` 指向管道一直在写的 `/share/homes/hardass/zhihu-pipeline/notes`，模式选 `daemon`（**传播删除**，因为用户会在设备上故意删笔记、把笔记移进自己的分类目录，`mirror` 不传播删除会让 NAS 留下旧副本再推回所有设备）。代价：NAS 侧缺文件同样会被当成删除，所以首次追平期间不要在其它设备编辑。之后删除 808MB 的 `zhihu-vault` 试验目录，GitHub 停用后再删 `.git`（回收 2.6GB）。此前 `sync` 曾在 28 分钟后被人工中止，未向生产库写入任何文档（探针文档仍为 `not_found`），`node-data` 停在约 925MB 的半复制状态。
- notify 网关已修好（2026-10-07）：NAS 配置里那把 40 位 api_key 是过期的，真 key 是 64 位，存放在本机 `~/vibe/notify-gateway/.api_key`（网关按 `Authorization: Bearer` / `X-API-Key` / `?key=` 比对，再用 Worker 里的 `TG_BOT_TOKEN` 转成 Telegram 消息）。已就地写回 NAS 配置（保留 inode）并用管道自身的 Notifier 实测送达。GB10 打标端点仍返回 530，每轮固定若干篇 `tagging failed`，按用户要求不在本链路内处理。
- vault 仓库已停止跟踪 `.obsidian/plugins/*/main.js`、`styles.css`、`manifest.json` 以及 `obsidian-livesync/data.json`、`obsidian-git/data.json`（提交 `8b246dd`）：这些文件由 Obsidian 与每台设备各自管理，交给 git 会与 LiveSync 互相回滚，是当初 LiveSync 远端配置被抹平的 most likely 机制。
- 回滚点：NAS 源码 `backups/src-pre-livesync-fix-20261006-212911`（含被就地改坏的 `sync_engine.py`）；附件迁移 `backups/pre-attachm-20261007-080102`（正文真拷贝 + 附件硬链接）。

## 当前已知运行状态（2026-10-08）

- 代码仓库 `6a90e4b` 已推送并部署到 NAS：附件前缀方案回退，管道重新写入 `assets/<笔记标题>/`，`migrate_attachments.py`、`check-attachments`、节点 preflight 已删除。部署前逐一比对过 6 个源文件与 `d29109d` 的内容，NAS 上无本地漂移，可安全覆盖。
- `zhihu-pipeline` 容器已重启（`docker restart`，非重建）：`running`、`restarts=0`、`oom=false`，容器内 `--help` 只剩 6 个子命令，证明进程加载的是回退后的代码。`git.sync_mode` 仍是 `git`，投递未受影响。
- 实测磁盘：`/share/CACHEDEV3_DATA` 余 329GB（`mem_limit` 的顾虑是内存不是磁盘），NAS `free -m` 显示约 10GB 空闲 + 11GB cache。`notes` 4.3G（含 `.git` 2.6G）、`zhihu-vault` 808M、`node-data` 923M。
- **数据事故（2026-10-07 23:36，2026-10-08 排查并核实）**：那次"停止跟踪 `.obsidian` 插件文件"的提交 `8b246dd` 之后，Mac 的 vault 磁盘内容**等于 git 工作树**（`git ls-files` 6774、磁盘 6777、未跟踪文件 0 个），说明紧随其后有一次覆盖式检出/清理，把所有 git 从未跟踪的文件一并删了。**真实损失核定为约 38 个内容文件 + 全部插件本体**，不是最初估计的 5900 个：
  - 插件本体（`main.js`/`manifest.json`/`styles.css`）全灭，含 LiveSync 与 dataview，Obsidian 里这些插件加载不了——可从官方 release / 插件市场重装，不算内容损失；
  - 真实内容缺 38 个：4 篇知乎笔记（`tools.md`、`codex 额度不够用`、`obsidian 的 13 种内容形式`、`2026年9月ai大模型大乱斗`）及其 10 张配图、`Jobs/` 12 个、`OneNote/` 6 个、`personal assets/assets/` 3 个、`Kid Edu/…/**While 的用法**.md`、`assets/仅一个文件，暴涨了.md`；
  - 另有 10 个是历史垃圾（4 个 `__orchestrator_smoke__*` 探针、`_node_probe.md`、`conflict-files-obsidian-git.md`、`obsydian_livesync_version`、`process_files.py`、根目录日记碎片），1 个是用户故意删的那篇；
  - **最初"约 5900 个文件消失"是错的**：数据库里 12,631 个文件文档中有 **5,860 个是陈旧重复文档**——同一内容以另一条路径存在（`assets/<标题>/…` 是迁到 `assets/知乎附件/` 之前的旧路径；`personal assets/assets/…` 是后来被挪进 `assets/` 的；还有一批 `xxx 1.md` 重名副本）。这些移动当初是在 git/Finder 里做的，LiveSync 没看见，所以旧文档一直活着。**在 Mac 上按大小写折叠比对之后，导出集与磁盘的差集为 0**——即节点能落出的 6,716 个文件 Mac 全都有。
- **陈旧重复文档是一个待办的真实风险，不是一次性噪声**：任何从头重建索引的客户端（新设备、Mac 强制重新获取、节点复制补齐）都会把这 5,860 个旧路径当成独立文件落盘，变成库里的重复笔记/重复附件。清理是不可逆的服务器写操作，做之前必须整库备份，并且判定标准不能只看文件名重合，要比对内容哈希。
- 已做的止血：从官方 release 取回 `obsidian-livesync` **1.0.35** 的 `main.js`/`manifest.json`/`styles.css` 放回 Mac 的插件目录（与节点 `1.0.35-cli` 同代，避免 compatibility paused）。**缺 `data.json`**（那份在 `8b246dd` 之前是被 git 跟踪的，但那是 1.0.21 时代的旧配置，不作为恢复来源）——需要用户在手机或 iPad（配置完好）上用 `Copy settings as a new Setup URI` 生成，Mac 端导入，然后让 Mac 把缺失文件拉回来。
- 实测 CouchDB 现状（供对账）：`obsidian-vault` `doc_count=66598`，其中**文件文档 12631 个、分块文档 53967 个**（`h:+…` 键），删除记录另有约 3.3 万条；`doc_count` 不等于文件数，别拿它当笔记篇数。用户故意删掉的那篇与探针文档均返回 404，说明删除已传播、探针没有残留。
- 实测 LiveSync 的键规则：**文档 `_id` 是小写化的 vault 相对路径，原始大小写保存在 `metadata.path` 里**（例：id `jobs/gemini gems/ai context.md` ↔ path `Jobs/Gemini Gems/AI context.md`）。QNAP 的 `/share/homes` 经实测**区分大小写**，所以节点写盘要用 `metadata.path`，不能拿 id 当路径，否则会造出大小写重复的目录树。
- 管道投递已临时暂停：NAS `config.yaml` 的 `git.sync_mode` 就地改为 `none`（inode 未变），容器内 `status` 确认 `Publish Channel: none`、`Delivery Unconfirmed: 2`——暂停期间不会消费知乎收件箱，这是设计中的安全状态。
- 节点忽略名单必须含 `/\.obsidian/`（任意深度）与 `\.DS_Store$`：这个库里有**嵌套的 Obsidian 子库**（`Jobs/.obsidian/`、`Travel/Japan202606/.obsidian/`、`Investiment/.obsidian/`），它们各带自己的插件与 `github-sync.log`；不过滤就会整批进库，而日志会每轮增长。
- 尚未做：Mac 重新入列并把约 5900 个文件拉回（需用户在手机/iPad 上生成 Setup URI）、节点常驻（daemon 挂整库 `notes`）、管道 `livesync:` 配置与 `sync_mode` 从 `none` 翻到 `livesync`、故障演练、CouchDB 重新加锁、GitHub 停用与 `.git` 删除。

## NAS 运维陷阱（实测）

- **单文件 bind mount 只认 inode。** 用编辑器保存或 `sed -i` 改 `config.yaml` 会替换 inode，容器内读到的仍是旧内容，必须重启容器才会重新解析绑定路径。原地写入请用 `cat new > old`，或改完固定重启一次。
- 判断容器是否真的加载了新代码：`src` 是 live mount，但 Python 进程只在启动时导入，**改完源码同样需要重启容器**；`docker exec ... python -m zhihu_pipeline status` 可以另起进程验证新代码。
- 镜像的 `ENTRYPOINT` 已经是 `python -m zhihu_pipeline`，`docker run` 只传子命令。写成 `docker run ... python -m zhihu_pipeline <cmd>` 会执行 `python` 这个不存在的子命令，而 Click 的错误照样打到 stdout/stderr，容易误读成"没有输出=成功"；看原始输出，不要只看退出码。
- `docker exec` 转发 stdin 必须加 `-i`，否则需要口令的 CLI（如 livesync `setup`）会静默读到空输入。
- NAS 上没有 `python3`、`comm`、`timeout`、`grep -P`（BusyBox 环境）；跨端脚本要用 POSIX 工具，或把逻辑放进容器内执行。
- 校验 git 脏路径必须用 `git status --porcelain -z`：路径含空格时 git 一律加引号，`core.quotepath=off` 管不了这个，按行解析会误判成越界改动。
- **摘除 git 跟踪 ≠ 删除文件，但紧随其后的检出/清理会。** `git rm --cached` 只动索引；可是文件一旦变成"未跟踪"，之后的 `git checkout .`、`git restore`、`git clean -fd` 就会把它们当作垃圾清掉。vault 这种仓库里，插件本体、`personal assets/`、`onenote/`、大量附件都只存在于 LiveSync 而不在 git 里，一旦误清就是几千个文件。摘除跟踪之后**立刻**用 `git status --porcelain -z --untracked-files=all` 确认未跟踪文件仍然在磁盘上，并且在此之前先做带日期的目录备份。
- **节点上传之前快照必须自洽。** 手工拼出来的 `zhihu-vault` 有笔记引用了没被拷进去的附件目录，节点把坏笔记推上线、覆盖掉各设备本地的好版本，实测在 Mac 弹出 8 个冲突对话框、手机端出现 "This file has unresolved conflicts"。加白名单不能防这个（问题在内容不在路径），所以重建快照后必须先核对"笔记引用的附件目录都存在"再启动节点；重建一律用管道自己的输出目录，不要手拼。
