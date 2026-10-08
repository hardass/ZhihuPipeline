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
- 尚未做：节点常驻（daemon 挂整库 `notes`）、管道 `livesync:` 配置与 `sync_mode` 翻转、故障演练、CouchDB 重新加锁、GitHub 停用。

## NAS 运维陷阱（实测）

- **单文件 bind mount 只认 inode。** 用编辑器保存或 `sed -i` 改 `config.yaml` 会替换 inode，容器内读到的仍是旧内容，必须重启容器才会重新解析绑定路径。原地写入请用 `cat new > old`，或改完固定重启一次。
- 判断容器是否真的加载了新代码：`src` 是 live mount，但 Python 进程只在启动时导入，**改完源码同样需要重启容器**；`docker exec ... python -m zhihu_pipeline status` 可以另起进程验证新代码。
- 镜像的 `ENTRYPOINT` 已经是 `python -m zhihu_pipeline`，`docker run` 只传子命令。写成 `docker run ... python -m zhihu_pipeline <cmd>` 会执行 `python` 这个不存在的子命令，而 Click 的错误照样打到 stdout/stderr，容易误读成"没有输出=成功"；看原始输出，不要只看退出码。
- `docker exec` 转发 stdin 必须加 `-i`，否则需要口令的 CLI（如 livesync `setup`）会静默读到空输入。
- NAS 上没有 `python3`、`comm`、`timeout`、`grep -P`（BusyBox 环境）；跨端脚本要用 POSIX 工具，或把逻辑放进容器内执行。
- 校验 git 脏路径必须用 `git status --porcelain -z`：路径含空格时 git 一律加引号，`core.quotepath=off` 管不了这个，按行解析会误判成越界改动。
- **节点上传之前快照必须自洽。** 手工拼出来的 `zhihu-vault` 有笔记引用了没被拷进去的附件目录，节点把坏笔记推上线、覆盖掉各设备本地的好版本，实测在 Mac 弹出 8 个冲突对话框、手机端出现 "This file has unresolved conflicts"。加白名单不能防这个（问题在内容不在路径），所以重建快照后必须先核对"笔记引用的附件目录都存在"再启动节点；重建一律用管道自己的输出目录，不要手拼。
