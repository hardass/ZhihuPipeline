# ZhihuPipeline

把知乎收藏夹当收件箱用：收藏一篇回答/文章/视频 → NAS 上的管道把它下载成 Obsidian 笔记 → headless LiveSync 节点发布到 CouchDB → Mac、手机、iPad 自动收到。确认送达之后，管道才把这条从知乎收藏夹里移除。

## 架构

```
知乎收藏夹
   │  Playwright 有头浏览器（真实点击，绕开 x-zse-96 签名）
   ▼
NAS 容器 zhihu-pipeline        每 ~2h 一轮，下载 Markdown + 图片 + 低清视频
   │  写入 /share/homes/hardass/zhihu-pipeline/notes
   ▼
NAS 容器 zhihu-livesync-node   daemon 模式，挂同一个 notes 目录
   │  发布到 CouchDB obsidian-vault（E2EE）
   ▼
Mac / 手机 / iPad 的 Obsidian + Self-hosted LiveSync
```

管道自己**不写 CouchDB**。它只用 `publish_probe` 去问"这份文档到了没有"，问到了才消费知乎收件箱；问不到就留着条目、报警，绝不静默丢弃。这是整套设计里唯一真正会丢数据的环节，所以它被做成 fail-closed。

投递通道由 `git.sync_mode` 决定：`livesync`（生产）、`git`（回滚用，待删）、`none`（安全暂停，不消费收件箱）。

## 部署

**1. 管道容器**

```bash
git clone git@github.com:hardass/ZhihuPipeline.git && cd ZhihuPipeline
cp config.example.yaml config.yaml   # 填 notify gateway、livesync 凭据
docker compose up -d --build
docker compose exec zhihu-pipeline python -m zhihu_pipeline check-auth   # 扫码登录知乎
```

生产入口是 `worker`（定时同步 + 可选 Telegram 控制面）。`config.yaml` 以单文件 bind mount 挂进容器，**改完必须重启容器**，而且原地写入要用 `cat new > old` 保住 inode。

**2. LiveSync 节点**

见 [`deploy/livesync-node/README.md`](deploy/livesync-node/README.md)。要点：镜像从上游 `src/apps/cli/Dockerfile` 构建；配置靠一台已配好的设备生成的 Setup URI 导入；节点挂**整库目录**、不设路径白名单；容器必须以 root 跑，否则 node-data 权限不对会静默不激活。

## 日常操作

```bash
docker compose exec zhihu-pipeline python -m zhihu_pipeline status   # 投递状态、待处理数
docker compose exec zhihu-pipeline python -m zhihu_pipeline sync     # 立刻跑一轮
docker compose exec zhihu-pipeline python -m zhihu_pipeline tag --dry-run
```

`status` 里的 `Delivery Waiting` 是"还没送达、会继续等"，`Delivery Unrecoverable` 是"记录在案但文件已不在磁盘上"，两者分开计。

## 配置要点

`config.example.yaml` 每一项都有注释。几个容易踩的：

- `sync.remove_after_sync: true` 才是收件箱模式；它受投递证明约束，不会先删源。
- `sync.video_quality: ld` + `video_max_size_mb: 50`：CouchDB 实测在 49.15MiB 附近拒收文档，50（十进制 MB）是刻意的安全边界。
- `output.manifest_path: .pipeline/manifest.json`：状态文件放在点目录里，Obsidian 不显示，同步通道也不会把它带到别的设备再带回来。
- `livesync.node_max_stale_minutes`：要远大于心跳间隔。实测空闲但健康的 CLI 节点可以 23 分钟不刷新心跳，卡太紧会误报。
- `tagger.enabled`：模型端点挂掉时应该关掉，否则每轮固定失败若干条并无限重试。

## 已知限制

- **无视频的想法（pin）不会被归档**。`_is_supported_item` 只在 pin 带可用视频时才处理，纯文字/图片 pin 被跳过且只打 DEBUG 日志，会一直留在收藏夹里。
- 附件链接是深度依赖的相对路径（`../../assets/...`），笔记被移到一级目录时会断链。修法见 `docs/AUDIT_2026-10-09.md`。
- CLI 节点无法靠复制建立本地库（"Replication result received, but not processed automatically in CLI mode"），它只能发布自己磁盘上的文件，不能把库里其他设备的内容取回来。

## 文档

- [`docs/AUDIT_2026-10-09.md`](docs/AUDIT_2026-10-09.md) — 当前真实状态、遗留缺陷、下一步
- [`docs/DESIGN.md`](docs/DESIGN.md) — 架构与模块设计
- [`docs/CHANGELOG.md`](docs/CHANGELOG.md) — 重大变更与实测结论
- [`AGENTS.md`](AGENTS.md) — Agent 运行规则与 NAS 运维陷阱

## 隐私

`config.yaml`、`.env`、`chrome_profile/`、`notes/`、`logs/` 全部在 `.gitignore` 里，凭据不会进版本库。通知走自建 notify-gateway（Cloudflare Worker），模型调用走局域网内的 GB10，两者都不经过商业 API。
