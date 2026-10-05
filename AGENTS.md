# ZhihuPipeline Agent 运行规则

## NAS / QNAP Docker

这个项目的生产容器运行在 QNAP NAS，不运行在当前 Mac 的本地 Docker 中。

- NAS：`hardass@192.168.1.195`；Tailscale fallback：`hardass@100.75.232.63` 或 `hardass@nas8c117a`。
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

## 当前已知运行状态（2026-09-16）

- `zhihu-pipeline` 已按用户授权重新启动；当前 Docker 状态为 `running`，重启次数为 0，`OOMKilled=false`。
- 生产容器已切换为 `worker` 入口，`telegram.enabled=false`，不再启动 Telegram polling；Telegram token 不再是生产主流程依赖。
- `notify-gateway` 保持启用，负责知乎登录失效时的二维码、登录成功和失败告警通知；其健康检查返回 HTTP 200。
- GB10 Qwen3-Coder-Next 已由 `gb10-coder-next-vllm.service` 托管，监听本机 `127.0.0.1:8002`；Cloudflare 专用 Tunnel active/enabled，公网 `/v1/models` 和最小聊天探针均返回 HTTP 200。
- Mac 工作树中验证过的 manifest 防护、原子写入、Git pull fail-closed、范围化 push、tagger timeout 和 Telegram 降级修复已部署到 NAS。
- NAS 配置中的 GitHub remote 已恢复有效认证；容器内 `git pull --rebase origin main` 已成功，工作树干净，当前相对 `origin/main` 为 `ahead 2`。
- 尚未主动触发完整知乎下载/打标签批处理，避免在验收前制造新的文章或提交；已完成 Git pull、GB10 tagger 预检和 worker 启动验收。
