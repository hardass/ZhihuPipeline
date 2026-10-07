# LiveSync 投递节点（NAS 侧）

知乎笔记从 NAS 到达 Mac / 手机的那一段链路。管道本身不写 CouchDB，这个容器负责把目录镜像进库。

## 前置条件（顺序不能换）

1. Mac 与手机的 Self-hosted LiveSync 已升级到与节点同代（节点用 `1.0.35-cli`，插件至少 `1.0.35`）。
   旧版本无法解除由新版本触发的 "Synchronisation paused for compatibility review"，会把 Mac↔手机这条活路也停掉。
2. `.obsidian/plugins/**/main.js`、`data.json` 已从 vault 的 git 跟踪中摘除（`git rm --cached` + `.gitignore`）。否则升级会被 `git pull` 打回旧版。
3. 已对 `couchdb-data` 与 vault 做过带日期的备份。
4. 投递目录是**知乎专用**目录（默认 `/share/homes/hardass/zhihu-vault`），里面不得有 `.git`、私人笔记或整库副本。

## 建镜像与初始化

```bash
git clone --depth 1 https://github.com/vrtmrz/obsidian-livesync.git
cd obsidian-livesync
docker build -f src/apps/cli/Dockerfile -t zhihu-livesync-node:latest .

mkdir -p /share/homes/hardass/livesync-node/livesync-data
cp livesync-settings.template.json /share/homes/hardass/livesync-node/livesync-data/livesync-settings.json
chmod 600 /share/homes/hardass/livesync-node/livesync-data/livesync-settings.json
```

配置只从 Setup URI 导入，不要手抄口令：

```bash
docker run --rm -v "$PWD/livesync-data:/data" -e SETUP_URI_FILE=/data/setup.uri \
  zhihu-livesync-node:latest init-settings /data/livesync-settings.json
# 在 Mac 上执行 'Copy settings as a new Setup URI'（选 Compatible / no time limit），
# 把 URI 写入 livesync-data/setup.uri 并 chmod 600，然后：
docker run --rm -v "$PWD/livesync-data:/data" \
  -e SETUP_PASSPHRASE \
  zhihu-livesync-node:latest --settings /data/livesync-settings.json \
  setup "$(cat livesync-data/setup.uri)"
```

## 先跑 canary，再碰正式库

在 `livesync-settings.json` 里把 `couchDB_DBNAME` 改成 `obsidian-zhihu-canary`（用官方 provisioning wrapper 建库，它会同时初始化 database-version 文档），然后逐条验收：

| # | 检查 | 通过标准 |
|---|---|---|
| 1 | 往 vault 写一个测试文件 | 60 秒内 canary 库 `_all_docs` 命中该文档 |
| 2 | 跨版本共存 | 升级后的 Mac 与该节点双向各写一次，两边都不出现 paused-for-review、不产 conflict |
| 3 | 否定测试 | 节点启动前后 `doc_count` 与 key 集合**没有任何删除** |
| 4 | 范围测试 | `.git`、`.pipeline/manifest.json`、非知乎目录都没有进入库；库里只有 `^知乎收藏/` 与 `^assets/知乎视频/` |
| 5 | 边界测试 | 一个 40–50MB 视频能进库（CouchDB `max_document_size=50000000`） |

全部通过后才把 `couchDB_DBNAME` 改回 `obsidian-vault`，并在 Mac 的 Obsidian 里接受新节点（`accepted_nodes` 增加 `zhihu-node`）。

## 首次接入的顺序与两个实测坑（2026-10-07）

这两条都是真跑出来的，不是推测：

1. **白名单必须含 `^assets/知乎附件/`。** 知乎图片过去落在 `assets/<笔记标题>/`，那个命名空间里混着私人笔记的附件（实测 NAS 上 579 个附件目录里只有 180 个属于知乎）。LiveSync 的 `syncOnlyRegEx` / `syncIgnoreRegEx` 是**双向**生效的，没有"只推不拉"的表达方式，所以前缀必须干净：先跑 `python -m zhihu_pipeline migrate-attachments`（默认空跑）把知乎附件收敛到 `assets/知乎附件/`，再让节点接入。
2. **锁库（Lock Server）开关会把 `accepted_nodes` 重置。** 实测关掉锁之后，原本 3 个已接受节点只剩 1 个。因此**立刻重新加锁会把那两台还没重新注册的设备静默挡在门外**——正是本次要消灭的故障类型。正确顺序：解锁 → 新节点完成一次同步并注册 → 让其它已有设备各打开一次 Obsidian 重新入列 → 确认名单齐全 → 再恢复锁 → 验证锁着仍能同步。
3. **CLI 非交互时遇到 `locked` 会自动选"取消"**，不会写入任何东西（实测探针文档仍是 `not_found`）。这是安全行为，但也意味着它不会替你完成接受流程。
4. 首次 `sync` 会把远端全部文档元数据拉进节点本地库（实测远端 66k+ 文档时明显耗时），期间 `node-data` 会增大，属正常。


## 启动与观察

```bash
docker compose -f docker-compose.yml config          # 先看渲染结果
docker compose -f docker-compose.yml up -d
docker logs -f zhihu-livesync-node                   # 每轮 mirror + sync 的退出码
```

这里用的是 **cron 风格的 `mirror` + `sync` 循环**，不是 `daemon`。原因：`mirror` 不传播删除（磁盘上缺失的文件会被从库里恢复回来，真要删得显式用 `rm`），而 `daemon` 的 chokidar 会把 create/modify/**delete** 全部推给 CouchDB。在"节点只挂一个子树"的部分镜像拓扑下，前者误操作后果小得多。稳定跑满一周、且确认白名单无法被绕开后，再考虑切 `daemon --interval 60`。

## 与管道的接口

`zhihu-pipeline` 不读这个容器，也不会写它。它只在 CouchDB 上做**只读**验证：

- 逐文档存在性（`HEAD /obsidian-vault/<知乎收藏/….md>`）；
- `_local/obsydian_livesync_milestone` 里 `zhihu-node` 的心跳新鲜度。

两者都通过才认为"已发布"，才会从知乎收藏夹移除条目。节点停掉时，管道会告警并且不再消费收件箱——这正是这次事故缺的那个反馈回路。

配置项见 `../../config.example.yaml` 的 `livesync:` 段；凭据只走环境变量 `LIVESYNC_COUCHDB_URL/DB_NAME/USER/PASSWORD/NODE_NAME`。

## 回滚

停容器即可（`docker stop zhihu-livesync-node`）。CouchDB 里已写入的内容不受影响；管道侧把 `git.sync_mode` 改回 `git` 就恢复 GitHub 投递（新代码每轮重读 `config.yaml`，不用重启容器）。

## 不要做的事

- 不要把 vault 指成整库目录，也不要让它看见 `.git`。
- 不要在这个库上试用第三方 headless 重写版客户端。
- 不要打开 `use_path_obfuscation`：一旦开启，管道只能拿到游标级间接证据，代码会拒绝据此删除任何知乎源文件。
- 不要把 `livesync-settings.json`、`setup.uri` 提交进 Git 或贴进对话。
