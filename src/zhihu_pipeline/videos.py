import asyncio
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from zhihu_pipeline.storage import sanitize_filename


VIDEO_QUALITIES = ("ld", "sd", "hd", "fhd")


@dataclass(frozen=True)
class VideoAsset:
    """A downloadable rendition exposed by a Zhihu pin."""

    video_id: str
    url: str
    quality: str
    format: str = "mp4"
    size: Optional[int] = None
    duration: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None


class VideoDownloadError(RuntimeError):
    """Raised when a video cannot be downloaded and verified."""


def _video_blocks(raw_content: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        block
        for block in (raw_content.get("content") or [])
        if isinstance(block, dict) and block.get("type") == "video"
    ]


def _select_rendition(playlist: Dict[str, Any], preferred_quality: str) -> tuple[str, Dict[str, Any]]:
    preferred_quality = (preferred_quality or "ld").lower()
    if preferred_quality not in VIDEO_QUALITIES:
        preferred_quality = "ld"

    if preferred_quality in playlist and playlist[preferred_quality].get("url"):
        return preferred_quality, playlist[preferred_quality]

    available = [
        (quality, rendition)
        for quality in VIDEO_QUALITIES
        if (rendition := playlist.get(quality)) and rendition.get("url")
    ]
    if not available:
        raise VideoDownloadError("Zhihu video has no downloadable rendition")

    return min(available, key=lambda pair: pair[1].get("size") or float("inf"))


def extract_pin_video_assets(
    raw_content: Dict[str, Any], preferred_quality: str = "ld"
) -> List[VideoAsset]:
    """Extract one lowest/specified-quality asset for every video in a pin."""
    assets: List[VideoAsset] = []
    for index, block in enumerate(_video_blocks(raw_content), start=1):
        video_info = block.get("video_info") or {}
        playlist = video_info.get("playlist") or {}
        quality, rendition = _select_rendition(playlist, preferred_quality)
        video_id = str(block.get("video_id") or f"pin-video-{index}")
        assets.append(
            VideoAsset(
                video_id=video_id,
                url=str(rendition["url"]),
                quality=quality,
                format=str(rendition.get("format") or "mp4").lower(),
                size=int(rendition["size"]) if rendition.get("size") else None,
                duration=rendition.get("duration") or video_info.get("duration") or block.get("duration"),
                width=rendition.get("width") or video_info.get("width") or block.get("width"),
                height=rendition.get("height") or video_info.get("height") or block.get("height"),
            )
        )
    return assets


def extract_pin_detail(item: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a pin's collection payload into the detail shape used by SyncEngine."""
    raw = item.get("raw_content") or {}
    text_blocks = [
        block
        for block in (raw.get("content") or [])
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    title = next((block.get("title") for block in text_blocks if block.get("title")), None)
    title = title or item.get("title") or "知乎想法"
    html_parts = [
        block.get("content", "")
        for block in text_blocks
        if block.get("content", "").strip() not in {"", "-", "<p>-</p>"}
    ]
    author = raw.get("author") or {}
    author_name = author.get("name", "") if isinstance(author, dict) else ""
    return {
        "title": title,
        "content_html": "\n".join(html_parts),
        "author_name": author_name,
        "created_time": raw.get("created"),
        "vote_count": raw.get("like_count", 0),
    }


def _vault_video_path(note_name: str, asset: VideoAsset, output_dir: str) -> tuple[str, str]:
    safe_note_name = sanitize_filename(note_name)
    safe_video_id = sanitize_filename(asset.video_id)
    filename = f"video-{safe_video_id}-{asset.quality}.{asset.format}"
    assets_dir = os.path.join(output_dir, "assets", "知乎视频", safe_note_name)
    target_path = os.path.join(assets_dir, filename)
    vault_path = f"assets/知乎视频/{safe_note_name}/{filename}"
    return target_path, vault_path


async def download_videos(
    assets: List[VideoAsset],
    note_name: str,
    output_dir: str,
    max_size_mb: float = 50.0,
) -> List[Dict[str, Any]]:
    """Download and atomically verify pin videos, returning vault-relative paths."""
    if not assets:
        return []

    max_bytes = int(max_size_mb * 1_000_000) if max_size_mb > 0 else None
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.zhihu.com/",
    }
    results: List[Dict[str, Any]] = []
    created_paths: List[str] = []

    try:
        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=60.0) as client:
            for asset in assets:
                if max_bytes and asset.size and asset.size > max_bytes:
                    raise VideoDownloadError(
                        f"Video {asset.video_id} is {asset.size} bytes, above the {max_bytes}-byte limit"
                    )

                target_path, vault_path = _vault_video_path(note_name, asset, output_dir)
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                expected_size = asset.size

                if os.path.exists(target_path) and (not expected_size or os.path.getsize(target_path) == expected_size):
                    logger.info(f"Video already exists, skipping download: {target_path}")
                    results.append({"asset": asset, "local_path": target_path, "vault_path": vault_path, "size": os.path.getsize(target_path)})
                    continue

                part_path = f"{target_path}.part"
                total = 0
                try:
                    async with client.stream("GET", asset.url) as response:
                        if response.status_code not in (200, 206):
                            raise VideoDownloadError(
                                f"Video {asset.video_id} returned HTTP {response.status_code}"
                            )
                        content_type = response.headers.get("content-type", "")
                        if content_type and not (content_type.startswith("video/") or content_type == "application/octet-stream"):
                            raise VideoDownloadError(
                                f"Video {asset.video_id} returned unexpected content type: {content_type}"
                            )
                        with open(part_path, "wb") as handle:
                            async for chunk in response.aiter_bytes(1024 * 1024):
                                handle.write(chunk)
                                total += len(chunk)
                                if max_bytes and total > max_bytes:
                                    raise VideoDownloadError(
                                        f"Video {asset.video_id} exceeded the {max_bytes}-byte limit while downloading"
                                    )

                    if expected_size and total != expected_size:
                        raise VideoDownloadError(
                            f"Video {asset.video_id} size mismatch: expected {expected_size}, got {total}"
                        )
                    os.replace(part_path, target_path)
                    created_paths.append(target_path)
                finally:
                    if os.path.exists(part_path):
                        os.unlink(part_path)

                logger.info(f"Successfully downloaded video to: {target_path} ({total} bytes)")
                results.append({"asset": asset, "local_path": target_path, "vault_path": vault_path, "size": total})
                await asyncio.sleep(0.5)
    except Exception:
        for path in created_paths:
            try:
                os.unlink(path)
            except OSError:
                pass
        raise

    return results


def render_video_embeds(downloaded_videos: List[Dict[str, Any]]) -> str:
    """Render Obsidian-native video embeds for successfully downloaded videos."""
    if not downloaded_videos:
        return ""
    lines = ["## 视频", ""]
    for result in downloaded_videos:
        lines.append(f"![[{result['vault_path']}]]")
        lines.append("")
    return "\n".join(lines).strip()
