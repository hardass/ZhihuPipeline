# 导出 Setup URI 给 NAS 节点用（手工步骤）

你只需要做这一件事，做完告诉我，后面的建镜像、canary 验收、接正式库都由我来跑。

URI 里含 CouchDB 凭据和库加密口令，**不要贴进对话、不要提交 Git、不要留在 shell history 里**。

---

## 第 0 步：确认 Mac 插件已是 1.0.35

Obsidian → 设置 → 第三方插件 → 已安装 → **Self-hosted LiveSync**，版本号应为 `1.0.35`（你已升级，这步只是核对）。

顺手记下另外两个节点的状态，后面要用：LiveSync 设置里的设备/远端列表，或直接看 CouchDB 的 `accepted_nodes`。我这边实测到除你 Mac 之外还有两个节点停在 **1.0.21**，最后一次连接分别是 9-24 和 9-04（大概率是手机和另一台机器）。**它们正式接入前也要升到 1.0.35**，否则会被版本兼容暂停卡住——这一步不急，等节点验完再做。

---

## 第 1 步：生成一条新的 Setup URI

在 Mac 的 Obsidian 里：

1. 按 `Cmd+P` 打开命令面板。
2. 输入并选择 **`Self-hosted LiveSync: Copy settings as a new Setup URI`**。
   （注意：要用"由当前可用设备导出"的这条命令，而不是当初装服务器时那条，这样才能带上这台机器实际生效的远端配置和加密设置。）
3. 弹窗里输入一个**新的 URI 口令**（Passphrase）。这个口令和"库加密口令"是两个不同的东西，别复用。
4. 在 **Setup URI availability** 里选 **`Compatible (no time limit)`**。
   默认是 `Time-bound`：七天**固定 UTC 窗口**（不是从生成时刻起算），过期就要重新导出，迁移中途卡住很难看。
5. 点确定，复制那串以 `obsidian://setuplivesync?settings=` 开头的内容。

---

## 第 2 步：把它落到 NAS 上的一个 600 权限文件里

在 Mac 终端执行（`read -s` 保证不回显、不进 history）：

```bash
ssh -i ~/.ssh/id_ed25519 -o BatchMode=yes hardass@192.168.1.195 \
  'mkdir -p /share/homes/hardass/livesync-node && umask 077 && cat > /share/homes/hardass/livesync-node/setup.uri && wc -c < /share/homes/hardass/livesync-node/setup.uri'
```

命令会在终端等你输入。把刚复制的那整串 URI 粘贴进去，按回车，再按 `Ctrl+D` 结束。

它会打印写入的字节数（正常应该是几千字节，如果只有 1 位或 0，说明粘贴没成功，重来）。

---

## 第 3 步：把 URI 口令单独存一份

同一个目录下再放一个口令文件，**和 URI 分开放**（官方明确要求两者不要走同一渠道）：

```bash
read -s -p "Setup URI passphrase: " P && ssh -i ~/.ssh/id_ed25519 -o BatchMode=yes hardass@192.168.1.195 \
  'umask 077 && cat > /share/homes/hardass/livesync-node/setup.passphrase && wc -c < /share/homes/hardass/livesync-node/setup.passphrase' <<< "$P"
unset P
```

同时请把这两个文件的位置和口令记进你自己的密码管理器。口令丢了就只能重新导一条 URI；**库加密口令**丢了才是真的读不回数据。

---

## 第 4 步：核对一下（只读，安全）

```bash
ssh -i ~/.ssh/id_ed25519 -o BatchMode=yes hardass@192.168.1.195 \
  'ls -l /share/homes/hardass/livesync-node/ && head -c 32 /share/homes/hardass/livesync-node/setup.uri; echo'
```

期望看到：两个文件权限都是 `-rw-------`，属主 `hardass`，URI 开头是 `obsidian://setuplivesync?settin`。

到这里你的手工部分就结束了，告我一声。

---

## 我接下来会做什么（你不用动手，但可以盯着）

1. 在 NAS 上构建官方 headless CLI 镜像（`vrtmrz/obsidian-livesync` 的 `src/apps/cli/Dockerfile`）。
2. 用 `livesync-cli ... setup` 导入你这条 URI，生成 `livesync-data/livesync-settings.json`，并把同步范围钉死成白名单：只允许 `^知乎收藏/` 和 `^assets/知乎视频/`，显式排除 `.git`、`.obsidian`、`manifest.json`。
3. 先接一个**独立的 canary 库**跑四项验收：新文件 60 秒内入库、升级后的 Mac 与节点双向共存都不触发兼容暂停、节点启动不删除任何已有文档、库里只出现知乎子树。另外拿一个 40–50MB 边界视频验证 CouchDB 的 50,000,000 字节上限。
4. 全部通过才把 `db_name` 指向正式的 `obsidian-vault`，并在你的 Obsidian 里确认接受新节点（`accepted_nodes` 会多出一个 `zhihu-node`）。
5. 节点稳定后，再把 NAS 的 `git.sync_mode` 从 `git` 翻成 `livesync`（新代码每轮重读配置，这一行改了不用重启容器），并观察一轮"投递已确认 → 才消费收件箱"的日志。
6. 最后才彻底停用 GitHub（按你的决定），停用前做一次最终 push 留冷快照。

期间 GitHub 投递保持可用，所以链路调试不会中断你的笔记流。
