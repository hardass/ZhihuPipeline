# 设计文档：知乎收藏夹 → Obsidian Pipeline

## 1. 项目背景

### 1.1 目标
将知乎收藏夹中的文章/回答（文字+图片）自动保存为 Obsidian Markdown 文件，建立本地化知识库。

### 1.2 约束条件
- 收藏夹 7-8 个，总条目 <100，日增 1-2 条
- 只处理文字+图片内容，不处理视频
- 时效性要求低，手动触发即可
- 半自动方式（需手动登录 Chrome），不做全自动登录

### 1.3 设计原则
- 简单可靠优先，不追求性能和并发
- 与现有 Obsidian Vault 格式完全兼容
- 不做复杂的反爬对抗（通过真实浏览器规避）

---

## 2. 部署方案

**Mac 本地运行**（非树莓派）

理由：
- Chrome Remote Debugging 需要 GUI 浏览器，Mac 原生支持
- 规模小，不需要 24h 运行
- 认证零成本，反爬风险最低
- 文件直接写入 Obsidian Vault，无需额外同步

---

## 3. 用户 Obsidian Vault 现状分析

Vault 路径：`/Users/hardass/notes`

### 3.1 Obsidian 配置

```json
// .obsidian/app.json
{
  "useMarkdownLinks": true,      // 用标准 Markdown 语法，不用 Wikilink
  "newLinkFormat": "relative",   // 使用相对路径
  "promptDelete": false
}
```

### 3.2 附件存储插件配置

安装了 `obsidian-custom-attachment-location` 插件：

```json
{
  "attachmentFolderPath": "./assets/${noteFileName}",
  "generatedAttachmentFileName": "file-${date:YYYYMMDDHHmmssSSS}",
  "shouldRenameAttachmentFiles": true
}
```

含义：每篇笔记的附件存放在 `./assets/笔记名/` 子目录下。

### 3.3 已有文件格式示例

来自 Clippings 目录的知乎想法(Pin)：

```markdown
---
title: "文章标题..."
source: "https://www.zhihu.com/pin/xxx"
author:
  - "[[作者名]]"
published:
created: 2026-06-29
description: "文章描述..."
tags:
  - "clippings"
---
正文内容...

![](https://picx.zhimg.com/v2-xxx.jpg)
```

关键观察：
- Front Matter 字段：title, source, author, published, created, description, tags
- author 使用 `[[双链]]` 格式
- 图片当前**直接引用知乎 CDN**，没有下载到本地
- tags 包含 `clippings`
- 内容中包含噪音（热搜、推荐等）

### 3.4 我们需要改进的

| 现状 | 改进 |
|---|---|
| 图片引用远程 URL（会过期） | 下载到本地 `./assets/笔记名/` |
| 包含热搜、广告等噪音 | 只保留正文内容 |
| 手动逐篇剪藏 | 一键批量同步 |
| 无评论 | 可选保存热门评论 |
| 无增量判断 | manifest.json 自动比对 |

---

## 4. 架构设计

### 4.1 模块划分

```
┌──────────────────────────────────────┐
│           CLI 入口 (__main__.py)       │
│    sync / status / check-auth        │
└──────────────┬───────────────────────┘
               │
┌──────────────▼───────────────────────┐
│         Sync Engine (sync_engine.py)  │
│   主调度循环：比对→获取→解析→保存     │
└──┬───────────┬───────────┬───────────┘
   │           │           │
┌──▼──┐   ┌───▼───┐   ┌───▼───┐
│Auth │   │Fetcher│   │Output │
│     │   │Parser │   │Storage│
│     │   │Images │   │       │
│     │   │Comment│   │       │
└─────┘   └───────┘   └───────┘
```

### 4.2 模块职责

#### auth.py — 认证模块
- 连接 Chrome CDP（`connect_over_cdp("http://localhost:9222")`）
- 访问 `/api/v4/me` 验证登录态
- 登录失效时给出明确提示

#### fetcher.py — 数据获取模块
- 获取收藏夹列表：`/api/v4/collections/mine`
- 获取收藏夹内容：`/api/v4/collections/{id}/contents`（分页）
- 打开单篇页面获取完整内容
- 双路径提取：XHR 拦截优先，DOM 选择器备选

#### parser.py — 内容解析模块
- HTML → Markdown 转换（markdownify + 自定义规则）
- 知乎特有元素处理（数学公式、链接跳转还原、代码块）
- 去除噪音（热搜、推荐、广告）

#### comments.py — 评论获取模块
- 获取热门评论：`/api/v4/answers/{id}/comments` 或 `/api/v4/articles/{id}/comments`
- 上限 20 条，失败静默跳过
- 格式化为 `<details>` 折叠区块

#### images.py — 图片处理模块
- 从 HTML 中提取所有 `<img>` 的 src URL
- 下载到 `./assets/笔记名/` 目录
- 文件命名匹配插件格式：`file-YYYYMMDDHHmmssSSS.ext`
- 替换 Markdown 中的图片路径为本地相对路径

#### storage.py — 存储模块
- 生成带 Front Matter 的 Markdown 文件
- 管理 `manifest.json` 的读写
- 文件名处理（去特殊字符、截断、去重）
- 目录结构创建

#### sync_engine.py — 主调度引擎
- 编排整个同步流程
- 增量比对逻辑
- 随机延迟控制（3-8 秒）
- 进度报告输出

#### config.py — 配置模块
- 加载 config.yaml
- 提供默认值

#### __main__.py — CLI 入口
- `sync` 命令：增量/全量同步
- `status` 命令：查看同步状态
- `check-auth` 命令：检查登录态

---

## 5. 数据流详解

### 5.1 收藏夹列表 API

**请求方式**：在浏览器中打开 `https://www.zhihu.com/collections/mine`，拦截 XHR

**响应结构**：
```json
{
  "paging": {
    "is_end": false,
    "next": "...?offset=20&limit=20",
    "totals": 8
  },
  "data": [
    {
      "id": 123456789,
      "title": "默认收藏夹",
      "item_count": 42,
      "updated_time": 1720000000,
      "is_default": true
    }
  ]
}
```

### 5.2 收藏夹内容 API

**请求方式**：打开 `https://www.zhihu.com/collection/{id}`，拦截 XHR

**响应结构**：
```json
{
  "data": [
    {
      "type": "answer",
      "id": 12345,
      "url": "https://www.zhihu.com/question/xxx/answer/12345",
      "title": "...",
      "content": "<p>HTML内容...</p>",
      "question": {
        "title": "问题标题",
        "url": "..."
      },
      "author": {
        "name": "张三",
        "url_token": "zhangsan"
      },
      "voteup_count": 1234,
      "created_time": 1700000000
    }
  ]
}
```

### 5.3 内容提取的双路径

**路径 A（主）- XHR 拦截**：
```python
collected_responses = []

async def on_response(response):
    if "/api/v4/" in response.url and response.status == 200:
        data = await response.json()
        collected_responses.append(data)

page.on("response", on_response)
await page.goto(url)
await page.wait_for_load_state("networkidle")
```

**路径 B（备）- DOM 选择器**：
```python
# 回答页面
title = await page.text_content("h1.QuestionHeader-title")
content_html = await page.inner_html("div.RichContent-inner")
author = await page.get_attribute("div.AuthorInfo meta[itemprop='name']", "content")

# 文章页面
title = await page.text_content("h1.Post-Title")
content_html = await page.inner_html("div.Post-RichTextContainer")
```

### 5.4 评论 API

```
GET /api/v4/answers/{answer_id}/comments?limit=20&offset=0&order=normal
GET /api/v4/articles/{article_id}/comments?limit=20&offset=0&order=normal
```

**响应结构**：
```json
{
  "data": [
    {
      "author": {"name": "李四"},
      "content": "评论内容...",
      "created_time": 1700100000,
      "vote_count": 5
    }
  ]
}
```

---

## 6. HTML → Markdown 转换规则

| 知乎 HTML | 转换为 Markdown |
|---|---|
| `<p>` | 普通段落 |
| `<h2>`, `<h3>` | `##`, `###` |
| `<strong>` / `<em>` | `**粗体**` / `*斜体*` |
| `<a href="link.zhihu.com/?target=URL">` | `[text](还原后的真实URL)` |
| `<figure><img src="...">` | `![alt](本地路径)` — 图片下载后替换 |
| `<img class="ztext-math" data-tex="...">` | `$LaTeX$` |
| `<pre><code class="lang-python">` | ` ```python ` |
| `<blockquote>` | `> 引用` |
| `<ul>/<ol>` | `- ` / `1. ` |
| `<table>` | Markdown 表格 |
| 知乎 zhida 链接 | 还原为原始文本或正常链接 |
| `<noscript>` 内的 img | 提取真实图片 URL |

**需要过滤的噪音内容**：
- 热搜推荐（底部的"微信迎来..."等内容）
- "还没有人送礼物" 提示
- "继续追问" 区块
- "更多回答" 推荐
- 知乎直答推荐链接

---

## 7. 增量同步机制

### manifest.json 结构

```json
{
  "version": 1,
  "last_sync": "2025-07-13T14:00:00+08:00",
  "synced_items": {
    "answer_123456": {
      "title": "文章标题",
      "type": "answer",
      "synced_at": "2025-07-13T14:02:30+08:00",
      "local_path": "默认收藏夹/文章标题.md",
      "zhihu_url": "https://www.zhihu.com/question/xxx/answer/123456",
      "collection": "默认收藏夹"
    }
  }
}
```

### 增量逻辑

1. 加载 manifest
2. 获取所有收藏夹的当前条目列表
3. 对比：manifest 中不存在的 = 新条目
4. 逐条处理新条目
5. 每处理一条就更新 manifest（简单断点续传）
6. manifest 中存在但收藏夹已没有的 → 标记 `removed`，不删文件

---

## 8. 配置文件设计

```yaml
# config.yaml

chrome:
  debug_port: 9222

sync:
  collections: "all"
  include_comments: true
  max_comments: 20
  delay_min: 3
  delay_max: 8

output:
  vault_path: "/Users/hardass/notes"
  collection_dir: "知乎收藏"
  image_naming: "file-${date:YYYYMMDDHHmmssSSS}"

selectors:
  answer:
    question_title: "h1.QuestionHeader-title"
    content: "div.RichContent-inner"
    author: "div.AuthorInfo meta[itemprop='name']"
    vote_count: "button.VoteButton--up"
    time: "div.ContentItem-time"
  article:
    title: "h1.Post-Title"
    content: "div.Post-RichTextContainer"
    author: "div.AuthorInfo meta[itemprop='name']"
    time: "div.ContentItem-time"
```

---

## 9. CLI 设计

```bash
# 增量同步所有收藏夹
python -m zhihu_pipeline sync

# 同步指定收藏夹
python -m zhihu_pipeline sync --collection "默认收藏夹"

# 全量重新同步
python -m zhihu_pipeline sync --full

# 查看同步状态
python -m zhihu_pipeline status

# 检查登录态
python -m zhihu_pipeline check-auth
```

---

## 10. 错误处理

| 场景 | 处理 |
|---|---|
| Chrome 未启动 | 提示运行 start_chrome.sh，退出 |
| 登录态失效 | 提示在 Chrome 中登录，退出 |
| 单条提取失败 | 跳过，继续下一条 |
| 图片下载失败 | 保留远程 URL，标注失败 |
| 评论被限制 | 静默跳过 |
| 网络超时 | 重试 1 次 |
| 视频类型 | 跳过，manifest 标记 unsupported |
| 同名文件 | 加上知乎 ID 后 6 位做后缀 |
