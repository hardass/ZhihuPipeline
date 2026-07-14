import re
from datetime import datetime
from bs4 import BeautifulSoup
from loguru import logger
from playwright.async_api import Page

def clean_comment_content(content: str) -> str:
    """
    Clean HTML tags from comment content, preserving text and formatting <br> tags.
    """
    if not content:
        return ""
    # Replace <br> and <br/> with newlines
    content_clean = re.sub(r'<br\s*/?>', '\n', content)
    # Parse with BeautifulSoup to strip other HTML tags (like <a class="member_mention">)
    soup = BeautifulSoup(content_clean, 'html.parser')
    return soup.get_text().strip()

async def fetch_comments(page: Page, content_type: str, content_id: str, max_count: int = 20) -> str:
    """
    Fetch popular comments for an answer or article and format them as a Markdown <details> block.
    Uses browser evaluate fetch to bypass signatures and cookies.
    """
    logger.info(f"Fetching comments for {content_type} {content_id}...")
    try:
        # Determine the api endpoint
        if content_type == "answer":
            api_url = f"/api/v4/answers/{content_id}/comments?limit=20&offset=0&order=normal"
        elif content_type == "article":
            api_url = f"/api/v4/articles/{content_id}/comments?limit=20&offset=0&order=normal"
        else:
            logger.warning(f"Unsupported content type for comments: {content_type}")
            return ""

        # Run fetch in page context
        js_code = f"""
        async () => {{
            try {{
                const resp = await fetch('{api_url}');
                if (resp.ok) {{
                    return await resp.json();
                }}
            }} catch (e) {{}}
            return null;
        }}
        """
        
        result = await page.evaluate(js_code)
        if not result or "data" not in result or not result["data"]:
            logger.info("No comments found or API request failed.")
            return ""

        comments_data = result["data"][:max_count]
        
        # Format comments as markdown
        formatted_comments = []
        for comment in comments_data:
            author = comment.get("author", {}).get("member", {}).get("name", "匿名用户")
            raw_content = comment.get("content", "").strip()
            content = clean_comment_content(raw_content)
            created_time_stamp = comment.get("created_time")
            
            # Format date
            date_str = ""
            if created_time_stamp:
                try:
                    date_str = datetime.fromtimestamp(created_time_stamp).strftime("%Y-%m-%d")
                except Exception:
                    pass
            
            if content:
                # Use clean HTML formatting since Markdown inside HTML blocks is not consistently parsed
                # and collapses newlines in many renderers.
                formatted_comments.append(
                    f"<strong>{author}</strong> · {date_str}<br>\n"
                    f"{content}"
                )

        if not formatted_comments:
            return ""

        # Construct the HTML details block with native HTML tags
        comment_block = "\n---\n\n<details>\n"
        comment_block += f"<summary>热门评论 ({len(formatted_comments)} 条)</summary>\n<br>\n"
        comment_block += "\n<br><hr><br>\n".join(formatted_comments)
        comment_block += "\n<br>\n</details>\n"

        logger.info(f"Successfully formatted {len(formatted_comments)} comments.")
        return comment_block

    except Exception as e:
        logger.error(f"Error fetching comments: {e}")
        return ""
