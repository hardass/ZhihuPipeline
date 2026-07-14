import re
import urllib.parse
from bs4 import BeautifulSoup
from markdownify import MarkdownConverter, markdownify as md
from loguru import logger

class ZhihuMarkdownConverter(MarkdownConverter):
    def convert_a(self, el, text, *args, **options):
        href = el.get('href', '')
        # If the link has been unwrapped by BeautifulSoup, it will just be text
        if not href:
            return text
        return super().convert_a(el, text, *args, **options)

    def convert_pre(self, el, text, *args, **options):
        if not text:
            return ''
        code_tag = el.find('code')
        lang = ''
        if code_tag:
            classes = code_tag.get('class', [])
            for c in classes:
                if c.startswith('language-'):
                    lang = c[9:]
                elif c.startswith('lang-'):
                    lang = c[5:]
        
        # Extract raw code text and strip it
        code_text = code_tag.get_text() if code_tag else el.get_text()
        return f'\n```{lang}\n{code_text}\n```\n'

def clean_zhihu_html(html: str) -> str:
    """
    Preprocess Zhihu HTML using BeautifulSoup to clean noise, unwrap special links,
    and resolve LaTeX/image duplicate issues.
    """
    if not html:
        return ""

    soup = BeautifulSoup(html, 'html.parser')

    # 1. Resolve noscript/lazy image duplication:
    # Keep the image inside <noscript> (which is the high-res one) and discard sibling imgs.
    for noscript in soup.find_all('noscript'):
        img = noscript.find('img')
        if img:
            parent = noscript.parent
            if parent:
                # Remove other images in the same parent (usually lazy-loaded duplicates)
                for other_img in parent.find_all('img'):
                    if other_img != img:
                        other_img.decompose()
            noscript.unwrap()

    # 2. Convert LaTeX Math formulas:
    # <img class="ztext-math" data-tex="E=mc^2" .../> -> $E=mc^2$
    for math_img in soup.find_all('img', class_='ztext-math'):
        tex = math_img.get('data-tex')
        if tex:
            math_img.replace_with(f"${tex}$")

    # 3. Restore Wrapped Links:
    # link.zhihu.com/?target=ENCODED_URL -> decoded URL
    for a in soup.find_all('a'):
        href = a.get('href', '')
        if 'link.zhihu.com/?target=' in href:
            parsed = urllib.parse.urlparse(href)
            query = urllib.parse.parse_qs(parsed.query)
            target = query.get('target', [''])
            if target and target[0]:
                a['href'] = urllib.parse.unquote(target[0])
        
        # 4. Unwrap Zhida (Keywords) Links:
        # zhida.zhihu.com -> keep text, remove link
        if 'zhida.zhihu.com' in href:
            a.unwrap()

    # 5. Remove Zhihu noise elements:
    # - "赞同", "还没有人送礼物", "继续追问", "更多回答"
    # - "发布于", "编辑于", "IP 属地"
    # - Bottom hot search links
    for el in soup.find_all(string=True):
        text_val = el.strip()
        if not text_val:
            continue
            
        # Match common boilerplate text patterns
        if (re.match(r'^赞同\s*\d+', text_val) or
            '还没有人送礼物' in text_val or
            text_val == '继续追问' or
            text_val == '更多回答' or
            re.search(r'发布于\s*\d{4}-\d{2}-\d{2}', text_val) or
            re.search(r'编辑于\s*\d{4}-\d{2}-\d{2}', text_val) or
            'IP 属地' in text_val):
            # Decompose the parent of this text if it contains only this text, otherwise clear text
            parent = el.parent
            if parent and len(parent.get_text(strip=True)) <= len(text_val) + 5:
                parent.decompose()
            else:
                el.replace_with("")

    # Remove hot search links containing search?q=
    for a in soup.find_all('a', href=re.compile(r'search\?q=')):
        a.decompose()

    return str(soup)

def html_to_markdown(html: str) -> str:
    """
    Convert Zhihu HTML body to Obsidian-compatible Markdown.
    """
    cleaned_html = clean_zhihu_html(html)
    
    # Run markdownify with custom converter to unwrap links nicely
    markdown = ZhihuMarkdownConverter(
        strip=['script', 'style'],
        heading_style="ATX"
    ).convert(cleaned_html)
    
    # Post-process markdown whitespace
    markdown = re.sub(r'\n{3,}', '\n\n', markdown)
    return markdown.strip()

def extract_metadata_from_html(html: str) -> dict:
    """
    Extract backup metadata fields directly from page HTML if XHR fails.
    """
    meta = {
        "author": "",
        "vote_count": 0,
        "created_time": None
    }
    
    if not html:
        return meta
        
    soup = BeautifulSoup(html, 'html.parser')
    
    # Author
    author_meta = soup.find('meta', itemprop='name')
    if author_meta:
        meta["author"] = author_meta.get('content', '')
        
    # Vote Count
    vote_btn = soup.find('button', class_='VoteButton--up')
    if vote_btn:
        vote_text = vote_btn.get_text()
        nums = re.findall(r'\d+', vote_text.replace(',', ''))
        if nums:
            meta["vote_count"] = int(nums[0])
            
    # Created/Edited Time
    time_meta = soup.find('meta', itemprop='datePublished')
    if time_meta:
        meta["created_time"] = time_meta.get('content', '')
        
    return meta
