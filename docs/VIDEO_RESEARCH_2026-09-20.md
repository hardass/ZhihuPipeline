# 知乎收藏视频下载调研报告

日期：2026-09-20  
范围：NAS 生产容器中的知乎登录态、当前「我的收藏」收藏夹；不执行常规 `sync`，不移除收藏，不更新 manifest，不触发 Git push。

## 结论摘要

1. 当前收藏夹里实际发现的是 **2 个知乎托管视频 + 1 个图片 pin**，不是 3 个视频。第三个 pin 的标题提到了视频，但页面和 API 都没有视频资源。
2. 两个视频都可以用现有 Python/Playwright 登录态发现资源，再用 HTTP 流式下载；FHD 原始档下载成功，下载字节数与知乎 API 声明值完全一致。
3. 两个视频的 FHD 总大小约 **58.88 MB（56.15 MiB）**；如果采用 HD，总大小约 **31.24 MB（29.79 MiB）**；LD 总大小约 **17.04 MB（16.25 MiB）**。
4. Obsidian 官方支持 `.mp4` 视频附件和 `![[附件]]` 嵌入，因此技术上可以放进 vault，并在支持相应编解码器的桌面/移动设备上播放。
5. 不建议把所有知乎视频默认以 FHD 放进当前 Git/Obsidian 保险箱。建议先采用两级策略：
   - 默认下载 HD，只有少量、明确需要保留的短视频进入 vault；
   - FHD 或较大的视频放到 vault 外的 NAS 媒体目录，笔记只保存元数据、缩略图和原始链接；若以后需要手机访问，再单独选择媒体同步方案。

## 1. 当前收藏内容核对

生产容器读取到的收藏夹 ID 为 `1003706171`，页面和接口均返回 3 个 pin：

| Pin | 实际内容 | 时长 | 结论 |
|---|---|---:|---|
| `2073016360385487049` | 《我发现长期错误坐姿真的超伤——骶髂关节会有压迫感，下背也僵僵的》 | 约 34 秒 | 视频，已成功下载 |
| `2075931935801713536` | 《油管400万粉丝博主——极简主义大神 Matt 的〈改变我人生的12个习惯〉》 | — | 图片 pin；没有 `<video>`、MP4、M3U8 或视频请求 |
| `2075525216982386340` | 《一个视频解决自由泳换气下沉呛水等5个常见问题》 | 约 107 秒 | 视频，已成功下载 |

第二个 pin 的文字内容虽然提到“爆款视频”，但它实际附加的是一张图片，不是视频。这个判断经过两层核对：

- 收藏 API 的 `content` 数组中媒体类型为 `image`；
- 直接打开 pin 页面后没有 `<video>`，也没有向知乎视频播放接口或 `vdn3.vzuu.com` 发起视频请求。

## 2. 下载测试方法

测试使用了生产容器已有的代码和登录态：

1. 用 `fetch_collection_items()` 读取收藏夹，不调用 `SyncEngine.run()`。
2. 从 pin 的原始 `content` 中读取 `video_info.playlist`，得到 `ld`、`hd`、`fhd` 三档 MP4 直链。
3. 先用 `Range: bytes=0-0` 验证服务端返回的总长度，再用 HTTP 流式写入文件。
4. 只把测试产物写到 NAS 项目外的独立目录：

   `/share/homes/hardass/zhihu-video-test-20260920`

5. 下载后核对 HTTP 状态、`Content-Type`、实际字节数和 SHA-256；容器临时副本随后已清理。

没有把带有短期 `auth_key` 的视频 URL 写入报告或配置。此类 URL 是临时签名地址，不能作为长期存储引用。

## 3. 资源大小和下载结果

知乎 API 给出的三档规格如下；实际下载选择了 FHD，用于验证最高质量档是否可完整保存。

| 视频 | FHD 1080×1920 | HD 720×1280 | LD 478×848 | 实测结果 |
|---|---:|---:|---:|---|
| 错误坐姿 | 7,338,990 B | 3,690,558 B | 2,054,665 B | FHD HTTP 200，完整下载 |
| 自由泳换气 | 51,540,678 B | 27,545,456 B | 14,982,577 B | FHD HTTP 200，完整下载 |
| 合计 | **58,879,668 B** | **31,236,014 B** | **17,037,242 B** | — |

实际 FHD 文件：

| 文件 | 大小 | SHA-256 |
|---|---:|---|
| `2073016360385487049__fhd.mp4` | 7,338,990 B | `d66feffb9ce1166de995f9a42564f7e1bd20271949a1543404ee193709329a6f` |
| `2075525216982386340__fhd.mp4` | 51,540,678 B | `05ce4711f71acfe79f9f1452fbe7a27d684c5ffb2d24b15247df6dcd7ebb7121` |

因此，当前的“能不能下载”答案是：**能；而且不需要抓取浏览器 blob，也不需要录屏，API 已提供可下载的 MP4 资源。**

## 4. 当前 vault 和 NAS 的容量基线

只读检查得到：

- NAS 文件系统：833.7 GB 总容量，517.1 GB 可用，使用率约 38%。
- Obsidian vault 工作区：约 5.1 GB。
- vault 的 `.git`：约 2.6 GB。
- vault 的 `assets`：约 2.5 GB。
- vault 中已有视频文件：13 个，合计 289,866,235 B，约 276.44 MiB。
- 当前 Git 仓库没有 `.gitattributes`，NAS 容器没有安装 Git LFS；已有视频是普通 Git 文件。
- 本次测试的两个 FHD 文件在 vault 外合计约 57 MiB，不影响当前保险箱内容。

`.git` 检查还报告了约 267 MiB 的现有 garbage 临时 pack；这不是本次下载产生的，暂未清理，后续可作为独立的仓库维护事项处理。

### 增长推导

本次两个视频的 FHD 平均约 29.44 MB。按同样分布估算：

- 3 个类似视频：约 88.32 MB 原始文件；
- 每月新增 3 个：约 1.06 GB/年原始文件；
- 每月新增 10 个：约 3.53 GB/年原始文件；
- 每天新增 1 个：约 10.75 GB/年原始文件。

如果视频进入普通 Git，工作区有一份，Git 历史通常还会保留一份或多份二进制 blob；视频对 Git 的增量压缩收益很低。因此用于规划时，不能只按“当前文件大小”预算，最好按 **文件本体 + Git 历史 + 其他设备副本** 估算。视频被重新编码或替换时，历史占用还会继续增加。

如果当前这两个视频改存 HD，文件体积约是 FHD 的 53.1%；LD 约是 FHD 的 28.9%。对手机/平板竖屏观看而言，HD 是更稳妥的默认折中。

## 5. GitHub 和 Obsidian 约束

GitHub 官方当前限制/建议：

- 普通 Git 单文件超过 100 MiB 会被阻止；超过 50 MiB 会收到警告。
- GitHub 建议仓库小于 1 GB，强烈建议小于 5 GB；仓库 `.git` 的推荐最大值为 10 GB。
- Git LFS 可以承载更大的文件，但会产生独立的存储和下载带宽计量；GitHub Free/Pro 文档列出的包含额度是 10 GiB 存储和 10 GiB 带宽，具体仍应以账号计划为准。

本次第二个 FHD 文件为 51,540,678 B，约 49.15 MiB，刚好低于 GitHub 的 50 MiB 警告线；这不代表后续视频也会低于限制。第一条视频则很小。

Obsidian 官方支持：

- 视频格式包括 `.mp4`、`.webm`、`.ogv`、`.mov`、`.mkv`；
- 可用 `![[文件名.mp4]]` 嵌入；
- 实际播放还取决于设备可用的编解码器；
- Obsidian Sync 的附件也计入存储和版本历史，Standard/Plus 的单文件和总容量限制不同，当前官方页面列出 Standard 单文件 5 MB、总容量 1 GB，Plus 单文件 200 MB、总容量 10–100 GB。

本项目走的是 GitHub/Obsidian Git，而不是 Obsidian Sync；因此 Obsidian 的“能播放”不等于 GitHub/移动端同步成本低。移动设备若要离线播放，必须先把二进制文件完整拉取到本地。

## 6. 推荐方案

### 推荐的第一版策略

1. **对视频 pin 增加专门处理分支**，不要把 pin 当成普通 answer/article 解析。
2. 默认选择 `hd`，把视频存放在 vault 的统一目录，例如：

   `assets/知乎视频/<pin_id>.mp4`

3. Markdown 只嵌入本地相对路径，并保存视频 ID、时长、分辨率、下载档位、原始 pin URL 等元数据。
4. 设置保护阈值：单文件接近 50 MiB 就不进入普通 Git；超过阈值只保留缩略图和外部/ NAS 媒体引用。
5. 暂不引入 Git LFS。当前 NAS 没有 LFS，移动端 Git 客户端链路也尚未针对 LFS 做过验收；引入后需要单独验证 Mac、手机、平板的 clone/pull、离线打开和额度行为。

### 何时使用 FHD

只有在视频包含小字、动作细节或明确需要原始清晰度时才选 FHD。当前第一个视频的 FHD 代价很小，第二个视频的 FHD 已接近 50 MiB，作为长期自动策略风险明显更高。

### 何时放到 vault 外

满足任一条件时，建议放到 NAS 媒体目录而不是 Git vault：

- 单文件超过 50 MiB；
- 需要保留 FHD/4K 原档；
- 视频数量达到几十个以上；
- 不需要在手机/平板离线查看；
- 同一视频可能被重新下载、转码或产生多个版本。

## 7. 下一步建议

本报告只完成了探测和下载验证，没有修改生产代码。下一步如果决定正式支持视频，可以先实现“只处理 pin 视频、默认 HD、单文件大小阈值、原子写入、失败不移除收藏、范围化 Git 提交”的最小闭环，再用这两个测试视频验收移动端播放和 Git 增长。

参考：

- [GitHub Repository limits](https://docs.github.com/en/repositories/creating-and-managing-repositories/repository-limits)
- [About large files on GitHub](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)
- [Git Large File Storage billing](https://docs.github.com/en/billing/concepts/product-billing/git-lfs)
- [Obsidian accepted file formats](https://obsidian.md/help/file-formats)
- [Obsidian embed files](https://obsidian.md/help/embeds)
- [Obsidian Sync plans and storage limits](https://obsidian.md/help/Obsidian%2BSync/Plans%2Band%2Bstorage%2Blimits)
