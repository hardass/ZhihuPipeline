# 视频、Git 与 Obsidian LiveSync 关系调查

日期：2026-09-20

## 结论先行

- vault 内的 `.git` 确实属于 vault 文件夹的一部分：Mac 当前约 2.3G，NAS 上约 2.6G。
- 但当前 LiveSync 配置明确不把 `.git` 同步到 CouchDB，因此手机/平板不会收到这 2G 多的 Git 历史、对象和 pack 文件。
- 当前手机同步后端不是 PostgreSQL，而是 NAS 上的 CouchDB 3 容器 `obsidian-couchdb`。
- LiveSync 自己不是“只存当前文件”的简单复制：它会保存文件块、修订历史、冲突、删除记录和 tombstone，因此 CouchDB 仍可能存在旧版本冗余。
- 当前 CouchDB 数据目录约 4.1G，其中 `obsidian-vault` 两个 shard 合计约 4.0G，另有 `obsidian-podcast` 约 163M。这是独立于 vault `.git` 的另一套存储。
- 如果视频不放进 vault，笔记仍然可以观看，但通常是点击 HTTPS 链接后由浏览器或媒体 App 播放；不能再依赖普通的 `![[本地视频.mp4]]` 内嵌方式。

## 1. 当前实际同步拓扑

```text
NAS / Mac vault 文件夹
├── 普通笔记、图片、附件
├── .obsidian/
└── .git/                         ← Git 历史；物理上在 vault 内

普通 vault 文件 ── LiveSync ──> NAS CouchDB ──> 手机 / 平板
                                  ├── 文件块
                                  ├── 修订历史
                                  ├── 冲突分支
                                  └── 删除记录 / tombstone
```

已核对 NAS 的实际配置：

| 项目 | 当前值/观察 |
|---|---|
| LiveSync 后端 | CouchDB；活动远端配置名称为 `CouchDB obsidian-sync.perilcrosser.com` |
| NAS 容器 | `obsidian-couchdb`，镜像 `couchdb:3` |
| CouchDB 数据目录 | `/share/homes/hardass/obsidian-livesync/couchdb-data` |
| vault 数据库 | `obsidian-vault`，两个 shard 文件约 1.9G + 2.1G |
| 另一个数据库 | `obsidian-podcast`，两个 shard 合计约 163M |
| `.git` 是否进入 LiveSync | 否；`syncInternalFiles=false`，并且忽略规则包含 `/.git/` |
| LiveSync 历史 | `useHistory=true` |
| LiveSync 单文件上限 | `syncMaxSizeInMB=50` |

## 2. 2.6G Git 历史会不会同步到手机？

结论：**按当前配置不会。**

Git 目录虽然在 vault 内，所以它会计入 Mac/NAS 的 vault 文件夹容量，也会被 `obsidian-git` 用于 pull、commit、push；但是 LiveSync 把 `.git` 当作内部/忽略目录，不会将其中的 Git object、pack、refs 和历史提交复制到 CouchDB，也不会让手机保存这 2G 多的内容。

因此需要区分三种容量：

1. **本地 vault 容量**：包含 `.git`，所以 Mac/NAS 上会看到 4–5G。
2. **GitHub/Git 仓库容量**：包含普通 Git 历史中的旧版本和二进制 blob。
3. **LiveSync/CouchDB 容量**：不包含 `.git`，但可能包含 LiveSync 自己的修订和删除历史。

## 3. LiveSync 是否会保存旧版本冗余？

会，至少存在这种机制，而且当前配置启用了 `useHistory=true`。LiveSync 官方文档说明，其数据库会保存文件 metadata、chunks、revision history、conflicts、deletions 和 tombstones；删除或缩短文件后，旧对象不会立即全部消失。Garbage Collection V3 可以清理不再引用的 chunks，但要求所有设备先完成同步，且 CouchDB compaction 后仍可能保留 tombstone。

这意味着：

- 手机不会拿到 Git 历史；
- 但 LiveSync 的 CouchDB 可能比“当前所有文件大小之和”大很多；
- 4.1G CouchDB 数据不能简单等同为 4.1G 当前笔记，也不能仅凭文件目录大小推算旧版本占比；
- 如果以后删除大量视频，CouchDB 不一定马上下降，需要在确认所有设备同步完成后，再评估 LiveSync 的 GC/compaction；这属于维护操作，不应现在直接执行。

LiveSync 官方还说明，`syncMaxSizeInMB` 会跳过超过该值的本地/远程文件变更。当前配置是 50 MB；同时 CouchDB 配置的 `max_document_size` 是 50,000,000 字节。因此之前测试的第二个 FHD 视频 51,540,678 字节虽然约为 49.15 MiB，但已经超过当前 50,000,000 字节边界，不能把它作为可靠的 LiveSync 文件。对应 HD 版本 27,545,456 字节，处在更安全的范围内。

## 4. 视频不放进 vault，笔记还能不能看？

能，但观看方式会改变。

### 方案 A：vault 内缩略图 + NAS HTTPS 视频链接（推荐外置方案）

笔记中保存：

```markdown
![[assets/知乎视频缩略图/2075525216982386340.jpg]]

[观看视频](https://你的媒体域名/zhihu/2075525216982386340.mp4)
```

视频存放在 NAS 独立媒体目录，由 Nginx、QNAP File Station、Nextcloud、Jellyfin 或其他带 HTTPS 的媒体服务提供访问。链接应使用稳定的媒体路径或受控分享地址，不要把知乎返回的带 `auth_key` 的临时 CDN URL 直接写进笔记。

优点：vault 和 LiveSync 很轻，视频可按需在线播放。缺点：通常是点击后跳到浏览器/媒体 App，不是 Obsidian 原生离线内嵌；需要手机能访问 NAS，并正确处理 HTTPS、认证和 Range 播放请求。

### 方案 B：HD 视频进入 vault，FHD/大文件外置（推荐当前项目）

- HD 小视频使用 `![[...mp4]]`，手机可以离线打开；
- FHD 或超过 50 MB 的视频放 NAS 媒体目录；
- 笔记同时保存缩略图和“观看 FHD”链接。

这是观看体验、移动端可用性和同步成本之间最平衡的方案。Obsidian 官方支持 MP4 视频附件和 `![[附件]]` 嵌入，但播放仍取决于设备编解码器；LiveSync 还会把附件计入远端容量和历史。

### 方案 C：单独使用媒体库

视频放入 Immich、Jellyfin、Plex、Nextcloud 或 QNAP File Station，笔记只放媒体库的分享链接。适合视频数量增长到几十个以上，或者希望按作者、标签、缩略图、播放历史管理。

优点是媒体管理和播放更专业；缺点是手机需要额外 App/登录，笔记内通常只是链接，不是原生嵌入。

### 方案 D：单独的媒体 vault

可以建立第二个只放视频的 vault，并单独配置 LiveSync。但主笔记与媒体 vault 之间不能像同一 vault 内的 `![[...]]` 那样可靠地使用相对内部链接，移动端还要维护两个 vault；除非后续视频量很大，否则不建议现在引入。

## 5. 最终建议

当前建议采用混合策略：

1. 视频 pin 默认下载 HD；
2. HD 且小于 50,000,000 字节的精选视频进入 vault，用 Obsidian 原生嵌入；
3. FHD、超过阈值或数量较多的视频进入 NAS 独立媒体目录；
4. 每篇笔记保留缩略图、视频 ID、时长、分辨率、下载档位和外部观看链接；
5. 先不要调整 LiveSync 的 50 MB 上限，也不要直接执行 GC/compaction；等确认移动端确实需要哪些视频后再做小范围验收。

参考：

- [LiveSync settings：文件大小上限](https://github.com/vrtmrz/obsidian-livesync/blob/main/docs/settings.md)
- [LiveSync troubleshooting：数据库、历史、chunks 和 Garbage Collection](https://github.com/vrtmrz/obsidian-livesync/blob/main/docs/troubleshooting.md)
- [Obsidian accepted file formats](https://obsidian.md/help/file-formats)
- [Obsidian embed files](https://obsidian.md/help/embeds)
- [Obsidian attachments](https://obsidian.md/help/attachments)
