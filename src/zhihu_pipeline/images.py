import os
import re
import asyncio
from datetime import datetime
import httpx
from loguru import logger

# Regex to find markdown images: ![alt](url)
IMAGE_REGEX = re.compile(r'!\[(.*?)\]\((https?://[^\s)]+)\)')

def get_high_res_url(url: str) -> str:
    """
    Attempt to convert a Zhihu image URL to its highest resolution version (_1440w).
    For example:
    - https://picx.zhimg.com/v2-xxx_720w.jpg -> https://picx.zhimg.com/v2-xxx_1440w.jpg
    """
    # Look for suffixes like _720w, _b, _r before the extension
    # Commonly: _720w, _80w, _hd, _qhd, _r
    pattern = r'(_\d+w|_[a-z]+)(\.(?:jpg|png|gif|webp|jpeg))'
    if re.search(pattern, url):
        return re.sub(pattern, r'_1440w\2', url)
    return url

async def download_single_image(
    client: httpx.AsyncClient,
    url: str,
    target_path: str,
    original_url: str
) -> bool:
    """
    Download a single image file, handling potential high-res fallback.
    """
    # 1. Try high-res URL first if different
    high_res_url = get_high_res_url(url)
    urls_to_try = [high_res_url] if high_res_url != url else []
    urls_to_try.append(url)
    
    for current_url in urls_to_try:
        try:
            logger.debug(f"Trying to download image from: {current_url}")
            response = await client.get(current_url, timeout=10.0)
            if response.status_code == 200:
                with open(target_path, "wb") as f:
                    f.write(response.content)
                logger.info(f"Successfully downloaded image to: {target_path}")
                return True
            elif response.status_code == 404 and current_url == high_res_url:
                logger.warning(f"High-res version 404, falling back to original URL: {url}")
                continue
            else:
                logger.warning(f"Failed to download from {current_url}: Status code {response.status_code}")
        except Exception as e:
            logger.warning(f"Error downloading from {current_url}: {e}")
            if current_url == high_res_url:
                continue
                
    return False

async def download_images(markdown_text: str, note_name: str, output_dir: str) -> str:
    """
    Find all Zhihu CDN images in markdown_text, download them to target assets folder,
    and replace their URLs with relative paths.
    """
    # Find all matches
    matches = IMAGE_REGEX.findall(markdown_text)
    if not matches:
        return markdown_text

    # Filter to only Zhihu CDN images (zhimg.com or zhihu.com)
    zhihu_images = []
    for alt, url in matches:
        if 'zhimg.com' in url or 'zhihu.com' in url:
            zhihu_images.append((alt, url))

    if not zhihu_images:
        return markdown_text

    # Set up assets directory
    # output_dir/assets/note_name/
    assets_dir = os.path.join(output_dir, "assets", note_name)
    os.makedirs(assets_dir, exist_ok=True)
    logger.info(f"Assets directory prepared: {assets_dir}")

    # Headers to mimic a real browser to prevent blocks
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.zhihu.com/"
    }

    # Track replacements to apply at the end
    replacements = {}
    
    # We will use datetime for millisecond timestamps.
    # To prevent duplicates if downloading quickly, we increment a millisecond counter.
    base_time = datetime.now()
    ms_offset = 0

    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        for idx, (alt, url) in enumerate(zhihu_images):
            # Calculate unique filename timestamp
            img_time = base_time.timestamp() + (ms_offset / 1000.0)
            img_datetime = datetime.fromtimestamp(img_time)
            timestamp_str = img_datetime.strftime("%Y%m%d%H%M%S") + f"{img_datetime.microsecond // 1000:03d}"
            ms_offset += 1

            # Extract extension from URL, defaulting to jpg
            ext = "jpg"
            # Strip query params
            clean_url = url.split("?")[0]
            for possible_ext in ["png", "gif", "webp", "jpeg", "jpg"]:
                if clean_url.lower().endswith(f".{possible_ext}"):
                    ext = possible_ext
                    break

            filename = f"file-{timestamp_str}.{ext}"
            
            # Obsidian only requires space characters to be encoded as %20 in Markdown links.
            # Other characters like '+' and Chinese characters should be kept literal to prevent lookup failures.
            encoded_note_name = note_name.replace(" ", "%20")
            encoded_filename = filename.replace(" ", "%20")
            
            target_path = os.path.join(assets_dir, filename)
            local_rel_path = f"../../assets/{encoded_note_name}/{encoded_filename}"

            logger.info(f"[{idx+1}/{len(zhihu_images)}] Processing image: {url}")

            success = False
            if os.path.exists(target_path):
                logger.info(f"Image already exists, skipping download: {target_path}")
                success = True
            else:
                success = await download_single_image(client, url, target_path, url)
                # Sleep between downloads to avoid getting blocked
                await asyncio.sleep(0.5)

            if success:
                replacements[url] = (local_rel_path, alt)
            else:
                replacements[url] = (url, f"{alt} [下载失败]")

    # Apply replacements to markdown_text
    # We need to replace exactly the matches to avoid corrupting other contents
    for url, (new_path, new_alt) in replacements.items():
        # Escape special regex chars in URL
        escaped_url = re.escape(url)
        # Regex to find exactly this image reference in markdown
        pattern = rf'!\[(.*?)\]\({escaped_url}\)'
        markdown_text = re.sub(pattern, f'![{new_alt}]({new_path})', markdown_text)

    return markdown_text
