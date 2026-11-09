# 分步执行指南：用 AI 模型实现知乎收藏夹 Pipeline

本文档是给你（项目负责人）的操作手册。每一步都提供了：
- **做什么**：明确的任务描述
- **前置条件**：开始前需要什么
- **提示词模板**：直接复制给 AI 编码模型使用
- **验收标准**：怎么判断这一步做完了
- **注意事项**：常见陷阱

---

## 总览：8 个实现步骤

| 步骤 | 内容 | 预计时间 | 依赖 |
|---|---|---|---|
| Step 0 | 项目初始化 | 5 分钟 | 无 |
| Step 1 | config.py — 配置加载 | 10 分钟 | Step 0 |
| Step 2 | auth.py — Chrome CDP 连接 | 15 分钟 | Step 1 |
| Step 3 | fetcher.py — 收藏夹数据获取 | 30 分钟 | Step 2 |
| Step 4 | parser.py — HTML 转 Markdown | 30 分钟 | Step 0 |
| Step 5 | images.py — 图片下载 | 20 分钟 | Step 4 |
| Step 6 | comments.py — 评论获取 | 15 分钟 | Step 3 |
| Step 7 | storage.py — 文件存储 + manifest | 20 分钟 | Step 4, 5 |
| Step 8 | sync_engine.py + __main__.py — 组装 | 30 分钟 | 全部 |

**推荐顺序**：Step 0 → 1 → 2 → 3（认证+获取链路通了） → 4 → 5（解析链路通了） → 6 → 7 → 8（组装）

Step 4 和 Step 5 可以与 Step 2、3 并行开发，因为它们之间没有代码依赖。

---

## Step 0: 项目初始化

### 做什么
创建项目骨架、安装依赖、配置 uv。

### 提示词

```
我需要你帮我初始化一个 Python 项目。项目在 /Users/hardass/vibe/ZhihuPipeline 目录下。

请完成以下工作：

1. 创建 pyproject.toml，项目名 zhihu-pipeline，Python >= 3.11，依赖：
   - playwright
   - markdownify
   - click
   - loguru
   - pyyaml
   - httpx（备用）

2. 创建 src/zhihu_pipeline/ 目录结构，包含以下空文件（每个文件只写一行注释说明用途）：
   - __init__.py
   - __main__.py
   - config.py
   - auth.py
   - fetcher.py
   - parser.py
   - comments.py
   - images.py
   - storage.py
   - sync_engine.py

3. 创建 tests/ 目录，包含 test_parser.py 和 fixtures/ 子目录

4. 创建 start_chrome.sh 脚本：
   - macOS 下启动 Chrome，指定 --remote-debugging-port=9222
   - 使用独立的 --user-data-dir="$HOME/.zhihu_pipeline/chrome_profile"
   - 添加执行权限

5. 将项目根目录下已有的 config.yaml 保留不动

6. 用 uv 初始化虚拟环境并安装依赖：uv sync
7. 安装 playwright 浏览器：uv run playwright install chromium

不要修改 docs/ 目录下的任何文件和 README.md。
```

### 验收标准
- `uv run python -c "import zhihu_pipeline; print('OK')"` 输出 OK
- `uv run playwright install chromium` 完成无报错
- `./start_chrome.sh` 能启动一个新的 Chrome 窗口

---

## Step 1: config.py — 配置加载

### 做什么
实现配置文件的加载和校验。

### 提示词

```
请实现 src/zhihu_pipeline/config.py 模块。

功能：
1. 读取项目根目录下的 config.yaml 文件
2. 解析为一个 Config dataclass 或字典
3. 提供合理的默认值（当 config.yaml 中缺少某个字段时）
4. 提供一个 load_config() 函数作为入口

配置文件的完整结构参见 docs/DESIGN.md 第 8 节。

默认值：
- chrome.debug_port: 9222
- sync.collections: "all"
- sync.include_comments: true
- sync.max_comments: 20
- sync.delay_min: 3
- sync.delay_max: 8
- output.vault_path: "~/notes"
- output.collection_dir: "知乎收藏"
- output.image_naming: "file-${date:YYYYMMDDHHmmssSSS}"

技术要求：
- 使用 Python dataclass 或 pydantic（不强制）
- 路径要展开 ~ 为绝对路径（os.path.expanduser）
- 如果 config.yaml 不存在，使用全部默认值并打印提示

请同时写一个简单的测试，验证默认值加载和自定义值覆盖。
```

### 验收标准
- 不存在 config.yaml 时能加载默认值
- 存在 config.yaml 时能正确覆盖
- `~` 路径被正确展开

---

## Step 2: auth.py — Chrome CDP 连接

### 做什么
实现连接 Chrome 调试端口和验证知乎登录态。

### 提示词

```
请实现 src/zhihu_pipeline/auth.py 模块。

功能：
1. connect_chrome(port: int) 函数：
   - 使用 playwright 的 async API
   - 调用 playwright.chromium.connect_over_cdp(f"http://localhost:{port}")
   - 连接失败时抛出明确异常，提示用户运行 start_chrome.sh
   - 返回 browser 和 context 对象

2. check_login(page) 函数：
   - 在已连接的 page 上访问 https://www.zhihu.com/api/v4/me
   - 检查响应：如果返回用户信息 JSON（包含 name 字段），说明登录有效
   - 如果返回 401/403 或重定向到登录页，说明登录失效
   - 返回 (is_logged_in: bool, username: str)

3. get_or_create_page(context) 函数：
   - 如果 context 中已有页面，复用第一个
   - 如果没有，创建新 page
   - 返回 page 对象

技术参考（我之前做过类似项目）：
```python
from playwright.async_api import async_playwright

async with async_playwright() as p:
    browser = await p.chromium.connect_over_cdp("http://localhost:9222")
    context = browser.contexts[0]
    page = context.pages[0] if context.pages else await context.new_page()
```

注意：
- 使用 async API（不是 sync API）
- 使用 loguru 记录日志
- 需要从 config.py 读取端口号
```

### 验收标准
- Chrome 未启动时，报错信息明确提示用户启动 Chrome
- Chrome 启动但未登录知乎时，check_login 返回 (False, "")
- Chrome 已登录时，check_login 返回 (True, "用户名")

### 手动测试方法
```bash
# 1. 先启动 Chrome
./start_chrome.sh
# 2. 在 Chrome 中登录知乎
# 3. 运行测试
uv run python -c "
import asyncio
from zhihu_pipeline.auth import connect_chrome, check_login, get_or_create_page

async def test():
    browser, context = await connect_chrome(9222)
    page = await get_or_create_page(context)
    ok, name = await check_login(page)
    print(f'登录态: {ok}, 用户: {name}')
    # 不要关闭 browser！它是用户的真实 Chrome

asyncio.run(test())
"
```

---

## Step 3: fetcher.py — 收藏夹数据获取

### 做什么
获取收藏夹列表和收藏夹内的条目。

### 提示词

```
请实现 src/zhihu_pipeline/fetcher.py 模块。

功能：

1. fetch_collections(page) 函数：
   - 在浏览器中导航到 https://www.zhihu.com/collections/mine
   - 拦截 page.on("response") 捕获 /api/v4/ 开头的 JSON 响应
   - 等待页面加载完成 (networkidle)
   - 解析收藏夹列表，返回 List[dict]，每个 dict 包含 id, title, item_count
   - 如果有分页（paging.is_end == false），继续滚动页面触发加载

2. fetch_collection_items(page, collection_id) 函数：
   - 导航到 https://www.zhihu.com/collection/{collection_id}
   - 拦截 XHR 获取 /api/v4/collections/{id}/contents 的响应
   - 处理分页：滚动页面或点击"加载更多"来触发下一页
   - 返回 List[dict]，每个 dict 包含：
     - type: "answer" | "article" | "pin" | "zvideo"
     - id: 知乎内容 ID
     - url: 内容页面 URL
     - title: 标题（回答类型则用问题标题）
     - 其他可用字段尽量提取

3. fetch_content_detail(page, item) 函数：
   - 根据 item 的 URL 在浏览器中打开该页面
   - 等待页面完全加载
   - 双路径提取内容：
     a. 优先：拦截 XHR 响应中的 JSON 数据
     b. 备选：用 CSS 选择器从 DOM 提取
   - CSS 选择器参见 docs/DESIGN.md 第 5.3 节
   - 选择器的值从 config.yaml 的 selectors 字段读取
   - 返回 dict 包含：title, content_html, author_name, author_url, 
     vote_count, created_time, question_title(仅answer类型)

关键技术点：
- 使用 page.on("response", handler) 拦截 XHR
- handler 中用 await response.json() 解析 JSON
- 使用 asyncio.Event 或列表收集异步回调的结果
- 页面滚动用 page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
- 每次页面导航前先等待上一次导航完成

注意：
- 所有函数都是 async
- 使用 loguru 记录日志
- 遇到非 answer/article 类型（如 zvideo），跳过并记录日志
```

### 验收标准
- fetch_collections 能返回你的所有收藏夹列表
- fetch_collection_items 能返回指定收藏夹中的所有条目
- fetch_content_detail 能提取单篇文章/回答的完整 HTML 内容

### 手动测试方法
```bash
# 确保 Chrome 已启动且已登录知乎
uv run python -c "
import asyncio
from zhihu_pipeline.auth import connect_chrome, get_or_create_page
from zhihu_pipeline.fetcher import fetch_collections

async def test():
    browser, context = await connect_chrome(9222)
    page = await get_or_create_page(context)
    collections = await fetch_collections(page)
    for c in collections:
        print(f'{c[\"title\"]} ({c[\"item_count\"]} 条)')

asyncio.run(test())
"
```

---

## Step 4: parser.py — HTML 转 Markdown

### 做什么
将知乎的 HTML 内容转换为干净的 Markdown。

### 前置条件
不依赖其他模块，可以独立开发和测试。

### 提示词

```
请实现 src/zhihu_pipeline/parser.py 模块。

功能：将知乎文章/回答的 HTML 内容转换为干净的 Markdown 文本。

1. html_to_markdown(html: str) -> str 函数：
   - 使用 markdownify 库做基础转换
   - 在此基础上添加以下自定义规则

2. 自定义转换规则：

   a. 链接还原：
      知乎会将外部链接包装为 link.zhihu.com/?target=ENCODED_URL
      需要提取 target 参数的值并 URL decode，还原为真实链接
   
   b. 数学公式：
      <img class="ztext-math" data-tex="E=mc^2" .../>
      转换为 $E=mc^2$
   
   c. 图片提取：
      不做下载，只返回 (markdown_text, image_urls: List[str])
      图片暂时用 ![](原始URL) 格式，后续由 images.py 替换路径
      注意：知乎图片可能在 <noscript> 标签内，也要提取
      注意：<figure> 标签里的图片要正确处理
   
   d. 代码块：
      <pre><code class="language-python"> 或 class="lang-python"
      转为 ```python 代码块，保留语言标记
   
   e. 知乎特殊元素清理：
      - 去除 zhida.zhihu.com 的搜索链接（知乎自动生成的关键词链接）
        这些链接的文本保留，只去掉超链接
      - 去除 "赞同 xxx" 文本
      - 去除 "还没有人送礼物" 文本
      - 去除 "继续追问"/"更多回答" 区块
      - 去除底部热搜推荐（通常包含 search?q= 链接）
      - 去除 "发布于xxxx-xx-xx" 和 "IP 属地xxx" 文本
      - 去除 "编辑于xxxx-xx-xx" 文本

3. extract_metadata_from_html(html: str) -> dict 函数：
   - 从 HTML 中提取可用的元数据（作为 DOM 选择器提取的补充）
   - 尽量提取：author, vote_count, created_time

技术要求：
- 使用 markdownify 的 MarkdownConverter 子类来自定义转换规则
- 可以结合 BeautifulSoup 做预处理（清理噪音元素）
- 输出的 Markdown 应该干净、可读
- 数学公式不要被 markdownify 当成普通文本

请同时在 tests/test_parser.py 中编写单元测试，覆盖以上每种转换规则。
测试用例可以手写 HTML 片段，不需要真实网页。
```

### 验收标准
- 链接被正确还原
- 数学公式被转为 `$...$`
- 代码块保留语言标记
- zhida 链接被清理（文本保留）
- 底部热搜噪音被去除
- `pytest tests/test_parser.py -v` 全通过

---

## Step 5: images.py — 图片下载

### 做什么
下载图片到本地，替换 Markdown 中的远程 URL。

### 提示词

```
请实现 src/zhihu_pipeline/images.py 模块。

功能：

1. download_images(markdown_text: str, note_name: str, output_dir: str) -> str 函数：
   - 从 markdown_text 中找到所有 ![alt](url) 格式的图片引用
   - 只处理知乎 CDN 图片（域名包含 zhimg.com 或 zhihu.com 的）
   - 将每张图片下载到: {output_dir}/assets/{note_name}/ 目录下
   - 文件命名格式：file-{YYYYMMDDHHmmssSSS}.{ext}
     - 时间戳精确到毫秒，每张图片递增 1ms 确保不重复
     - ext 从 Content-Type 或 URL 推断（jpg/png/gif/webp）
   - 替换 markdown_text 中的远程 URL 为本地相对路径
     如：![alt](assets/如何理解Docker的网络模型/file-20250713140230001.jpg)
   - 返回替换后的 markdown_text

2. 技术要求：
   - 使用 httpx 异步下载（或 playwright page.request）
   - 下载失败时：保留原始远程 URL，在 alt 文本中标注 "[下载失败]"
   - 已存在同名文件时跳过（支持重跑幂等）
   - 创建目录时用 os.makedirs(exist_ok=True)
   - 使用 loguru 记录每张图片的下载状态

3. 知乎图片 URL 的特殊处理：
   - 知乎图片 URL 可能带有尺寸后缀如 _720w, _1440w, _b, _r
   - 尽量获取最大尺寸的图片（如果 URL 中有 _720w，尝试替换为 _1440w）
   - 如果大尺寸版本 404，回退到原始 URL

注意：
- 不要并发下载太多图片，串行或最多 2-3 并发
- 每张图片下载后 sleep 0.5 秒
```

### 验收标准
- 图片被下载到正确的目录结构中
- Markdown 中的 URL 被正确替换为相对路径
- 下载失败时不崩溃，保留原始 URL

---

## Step 6: comments.py — 评论获取

### 做什么
获取文章/回答的热门评论。

### 提示词

```
请实现 src/zhihu_pipeline/comments.py 模块。

功能：

1. fetch_comments(page, content_type: str, content_id: str, max_count: int = 20) -> str 函数：
   - content_type 是 "answer" 或 "article"
   - 通过在浏览器 page 中请求评论 API 获取评论
   - API 端点：
     - 回答评论：/api/v4/answers/{content_id}/comments?limit=20&offset=0&order=normal
     - 文章评论：/api/v4/articles/{content_id}/comments?limit=20&offset=0&order=normal
   - 获取方式：用 page.evaluate 执行 fetch() 请求（这样自动带上 Cookie 和签名）
   
   示例：
   ```python
   result = await page.evaluate("""
       async () => {
           const resp = await fetch('/api/v4/answers/12345/comments?limit=20&offset=0&order=normal');
           return await resp.json();
       }
   """)
   ```

   - 解析评论数据，提取：author.name, content, created_time, vote_count
   - 最多取 max_count 条
   - 返回格式化后的 Markdown 字符串，用 <details> 包裹

2. 输出格式：

```markdown

---

<details>
<summary>热门评论 (N 条)</summary>

**作者名** · YYYY-MM-DD
评论内容...

**作者名** · YYYY-MM-DD
评论内容...

</details>
```

3. 错误处理：
   - API 返回 403 或错误 → 返回空字符串（不影响主流程）
   - 没有评论 → 返回空字符串
   - 使用 try/except 包裹整个函数，任何异常都返回空字符串并记录日志
```

### 验收标准
- 能获取到评论并格式化为 Markdown
- API 被限制时不崩溃，静默返回空字符串

---

## Step 7: storage.py — 文件存储 + manifest

### 做什么
生成 Markdown 文件、管理 manifest.json。

### 提示词

```
请实现 src/zhihu_pipeline/storage.py 模块。

功能：

1. generate_markdown(content: dict, comments_md: str) -> str 函数：
   - content 字典包含：title, content_markdown, author_name, author_url,
     zhihu_url, zhihu_type, question_title(可选), vote_count, 
     created_time, collection_name
   - 生成带 YAML Front Matter 的完整 Markdown 文件
   - Front Matter 格式必须与用户现有文件兼容：

   ```yaml
   ---
   title: "文章标题"
   source: "https://www.zhihu.com/..."
   author:
     - "[[作者名]]"
   published: YYYY-MM-DD          # 文章原始发布日期
   created: YYYY-MM-DD            # 本地保存日期（今天）
   description: "正文前100字..."
   tags:
     - clippings
     - zhihu
   ---
   ```

   - Front Matter 之后：
     - 一级标题 # 文章标题
     - 引用块：原始链接、作者、赞同数
     - 分隔线
     - 正文
     - 如果有评论，追加 comments_md

2. sanitize_filename(title: str, max_length: int = 80) -> str 函数：
   - 去除文件名不允许的特殊字符：/ \ : * ? " < > | #
   - 截断到 max_length 字符
   - 去除首尾空白
   - 如果截断了，确保不在汉字中间断开

3. ManifestManager 类：
   
   - __init__(manifest_path: str)
   - load() -> dict：读取 manifest.json，不存在则返回空结构
   - save(data: dict)：写入 manifest.json，使用 ensure_ascii=False
   - is_synced(unique_key: str) -> bool：检查条目是否已同步
   - add_item(unique_key: str, item_info: dict)：添加条目记录
   - mark_removed(unique_key: str)：标记条目为已移除
   - get_stats() -> dict：返回统计信息（已同步数、待同步数等）

   unique_key 格式："{type}_{id}"，如 "answer_123456"

4. save_markdown_file(content: str, filepath: str) 函数：
   - 创建必要的目录
   - 写入文件（UTF-8 编码）
   - 如果同名文件已存在，加上知乎 ID 后 6 位做后缀

注意：
- manifest.json 每次写入都用 json.dump with indent=2, ensure_ascii=False
- 文件路径中的中文要正确处理
```

### 验收标准
- 生成的 Markdown 文件格式与用户现有剪藏文件一致
- manifest 的增删查改正常
- 文件名特殊字符被正确处理

---

## Step 8: sync_engine.py + __main__.py — 组装

### 做什么
将所有模块组装成完整的同步流程和 CLI 入口。

### 提示词

```
请实现以下两个文件：

### A. src/zhihu_pipeline/sync_engine.py

主同步引擎，编排所有模块：

```python
class SyncEngine:
    def __init__(self, config):
        self.config = config
        self.manifest = ManifestManager(...)
    
    async def run(self, full_sync=False, target_collection=None):
        """主同步流程"""
        # 1. 连接 Chrome
        # 2. 检查登录态
        # 3. 获取收藏夹列表
        # 4. 对每个收藏夹：
        #    a. 获取条目列表
        #    b. 增量比对（full_sync 时跳过比对）
        #    c. 对每个新条目：
        #       - 打开页面提取内容
        #       - HTML 转 Markdown
        #       - 下载图片
        #       - 获取评论（可选）
        #       - 保存文件 + 更新 manifest
        #       - 随机延迟 3-8 秒
        # 5. 输出同步报告
    
    async def check_auth(self):
        """只检查登录态"""
    
    def show_status(self):
        """显示同步状态"""
```

关键逻辑：
- 增量比对：用 manifest.is_synced(f"{type}_{id}") 判断
- 随机延迟：random.uniform(config.sync.delay_min, config.sync.delay_max)
- 进度输出：每处理一条打印状态（标题、图片数、评论数、保存路径）
- 错误处理：单条失败不中断，记录日志后继续
- target_collection：如果指定了收藏夹名称，只同步那一个
- 最后打印汇总报告

### B. src/zhihu_pipeline/__main__.py

CLI 入口，使用 click：

```python
import click

@click.group()
def cli():
    """知乎收藏夹 → Obsidian 同步工具"""
    pass

@cli.command()
@click.option("--full", is_flag=True, help="全量同步（忽略增量）")
@click.option("--collection", default=None, help="指定收藏夹名称")
def sync(full, collection):
    """同步收藏夹到本地"""
    ...

@cli.command()
def status():
    """查看同步状态"""
    ...

@cli.command("check-auth")
def check_auth():
    """检查知乎登录态"""
    ...
```

CLI 输出风格参考：

```
连接 Chrome (localhost:9222)... OK
检查知乎登录态... OK (用户: xxx)
扫描收藏夹...
   - 默认收藏夹 (42 条)
   - 技术收藏夹 (23 条)

增量比对:
   - 已同步: 75 条
   - 新增: 5 条

开始同步...
   [1/5] 如何理解 Docker 的网络模型？
         正文 OK | 3张图片 OK | 5条评论 OK
         -> 知乎收藏/默认收藏夹/如何理解Docker的网络模型.md
         等待 5.2s...
   ...

同步完成!
   新增: 5 条 | 失败: 0 条 | 耗时: 1分32秒
```

使用 click.echo 或 loguru 输出。
```

### 验收标准
- `python -m zhihu_pipeline check-auth` 能正确检查登录态
- `python -m zhihu_pipeline status` 能显示状态
- `python -m zhihu_pipeline sync` 能完成端到端同步
- 生成的文件在 Obsidian 中打开格式正确

---

## 调试和迭代技巧

### 如果 AI 模型写的代码有 bug

给模型的修复提示模板：

```
运行以下命令时报错：
[粘贴命令]

错误输出：
[粘贴完整错误信息]

相关源代码在 src/zhihu_pipeline/[文件名].py。
请分析错误原因并修复。不要修改其他文件。
```

### 如果知乎页面结构变了

```
我在浏览器中打开了知乎的 [回答/文章] 页面，用开发者工具检查发现：
- 原来的选择器 [旧选择器] 找不到元素了
- 现在的 HTML 结构是 [粘贴新结构]

请更新 config.yaml 中的 selectors 配置，以及 fetcher.py 中相关的解析逻辑。
```

### 如果 XHR 拦截获取不到数据

```
fetcher.py 中通过 page.on("response") 拦截 XHR 时，没有捕获到预期的 /api/v4/ 响应。

可能的原因：
1. 知乎改用了不同的 API 路径
2. 数据是通过其他方式加载的（如 SSR）

请修改 fetcher.py，添加以下调试逻辑：
1. 打印所有被拦截的 URL
2. 如果 XHR 路径失败，回退到 DOM 选择器提取

同时在 fetch_content_detail 中实现完整的 DOM fallback 路径。
```

---

## 端到端测试清单

完成所有步骤后，按以下顺序测试：

1. [ ] `./start_chrome.sh` — Chrome 正常启动
2. [ ] 在 Chrome 中登录知乎
3. [ ] `python -m zhihu_pipeline check-auth` — 显示登录成功
4. [ ] `python -m zhihu_pipeline status` — 显示收藏夹状态
5. [ ] `python -m zhihu_pipeline sync --collection "默认收藏夹"` — 同步一个收藏夹
6. [ ] 打开 Obsidian，检查 `知乎收藏/默认收藏夹/` 下的文件
7. [ ] 确认图片正确显示（本地图片，不是远程 URL）
8. [ ] 确认评论在折叠区块中（如果有的话）
9. [ ] 在知乎收藏一篇新文章，再运行 sync，验证增量同步
10. [ ] `python -m zhihu_pipeline sync` — 全量同步所有收藏夹
