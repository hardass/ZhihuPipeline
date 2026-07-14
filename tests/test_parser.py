import pytest
from zhihu_pipeline.parser import html_to_markdown, clean_zhihu_html

def test_link_restoration():
    html = '<p>这是一个链接：<a href="https://link.zhihu.com/?target=https%3A//github.com/google/gemini">Gemini</a></p>'
    md = html_to_markdown(html)
    assert md == "这是一个链接：[Gemini](https://github.com/google/gemini)"

def test_zhida_link_unwrapping():
    html = '<p>关注一下 <a href="https://zhida.zhihu.com/search?q=Playwright" class="zhida-link">Playwright</a> 自动化框架。</p>'
    md = html_to_markdown(html)
    assert md == "关注一下 Playwright 自动化框架。"

def test_latex_formula():
    html = '<p>爱因斯坦方程是 <img class="ztext-math" data-tex="E=mc^2" src="math.jpg"/> 极其重要。</p>'
    md = html_to_markdown(html)
    assert md == "爱因斯坦方程是 $E=mc^2$ 极其重要。"

def test_noscript_image_deduplication():
    html = '''
    <figure>
        <noscript><img src="https://picx.zhimg.com/v2-original.jpg" alt="original"/></noscript>
        <img src="https://picx.zhimg.com/v2-lazy.jpg" class="lazy" alt="lazy"/>
    </figure>
    '''
    md = html_to_markdown(html)
    assert "v2-original.jpg" in md
    assert "v2-lazy.jpg" not in md

def test_code_blocks():
    html = '''
    <pre><code class="language-python">def hello():
    print("world")</code></pre>
    '''
    md = html_to_markdown(html)
    assert "```python" in md
    assert 'print("world")' in md

def test_noise_removal():
    html = '''
    <div>
        <p>正文内容比较好看。</p>
        <button class="VoteButton">赞同 1234</button>
        <div>还没有人送礼物</div>
        <div>继续追问</div>
        <div>更多回答</div>
        <p>发布于 2026-07-13 15:30 IP 属地上海</p>
        <p>编辑于 2026-07-13 15:30</p>
        <a href="https://www.zhihu.com/search?q=hot">热搜推荐词</a>
    </div>
    '''
    md = html_to_markdown(html)
    # The clean content should be preserved
    assert "正文内容比较好看。" in md
    # The noise elements should be completely gone
    assert "赞同 1234" not in md
    assert "还没有人送礼物" not in md
    assert "继续追问" not in md
    assert "更多回答" not in md
    assert "发布于" not in md
    assert "IP 属地" not in md
    assert "编辑于" not in md
    assert "热搜推荐词" not in md
