import os
import re
import json
import yaml
from datetime import datetime
from typing import Dict, Any, Optional
from loguru import logger

def sanitize_filename(title: str, max_length: int = 80) -> str:
    """
    Sanitize filename by removing invalid characters and truncating it safely.
    """
    # Remove chars: / \ : * ? " < > | #
    clean = re.sub(r'[/\\:*?"<>|#]', '', title)
    clean = clean.strip()
    
    # Safely truncate to max_length without cutting in the middle of a multi-byte UTF-8 char
    # In python, strings are unicode, so slicing by characters is safe.
    if len(clean) > max_length:
        clean = clean[:max_length]
        
    return clean

def format_date(timestamp_or_str: Any) -> str:
    """
    Format various input types into YYYY-MM-DD.
    """
    if not timestamp_or_str:
        return datetime.today().strftime("%Y-%m-%d")
        
    if isinstance(timestamp_or_str, (int, float)):
        try:
            return datetime.fromtimestamp(timestamp_or_str).strftime("%Y-%m-%d")
        except Exception:
            pass
            
    # Try parsing string format
    str_val = str(timestamp_or_str).strip()
    # Match YYYY-MM-DD
    match = re.search(r'\d{4}-\d{2}-\d{2}', str_val)
    if match:
        return match.group(0)
        
    return datetime.today().strftime("%Y-%m-%d")

def generate_markdown(content: Dict[str, Any], comments_md: str = "") -> str:
    """
    Generate Obsidian Markdown file text with Front Matter.
    """
    title = content.get("title", "Untitled")
    source = content.get("zhihu_url", "")
    author_name = content.get("author_name", "Anonymous") or "Anonymous"
    
    # Date formats
    published = format_date(content.get("created_time"))
    created = datetime.today().strftime("%Y-%m-%d")
    
    # Extract description: first 100 characters of clean markdown text
    body_md = content.get("content_markdown", "")
    # Remove markdown formatting for description
    clean_text = re.sub(r'[*_`#\[\]()!>\-]', '', body_md)
    clean_text = re.sub(r'\s+', ' ', clean_text).strip()
    description = clean_text[:100] + "..." if len(clean_text) > 100 else clean_text
    
    # Build front matter dictionary
    front_matter = {
        "title": title,
        "source": source,
        "author": [f"[[{author_name}]]"],
        "published": published,
        "created": created,
        "description": description,
        "tags": ["zhihu"]
    }

    # Inject tagging fields if present
    for key in ["domain", "concept", "level", "summary"]:
        if key in content and content[key] is not None:
            front_matter[key] = content[key]
    
    # Dump YAML front matter manually or using PyYAML to match user styling
    yaml_lines = yaml.dump(front_matter, allow_unicode=True, default_flow_style=False, sort_keys=False).strip()
    
    # Start assembling final file
    md_text = "---\n"
    md_text += yaml_lines + "\n"
    md_text += "---\n\n"
    
    # Article title
    md_text += f"# {title}\n\n"
    
    # Metadata blockquote
    author_link = f"[[{author_name}]]"
    vote_count = content.get("vote_count", 0)
    md_text += f"> 原始链接: [{source}]({source}) | 作者: {author_link} | 赞同数: {vote_count}\n\n"
    md_text += "---\n\n"
    
    # Body
    md_text += body_md.strip() + "\n"
    
    # Comments
    if comments_md:
        md_text += comments_md
        
    return md_text

def save_markdown_file(content_str: str, filepath: str, item_id: str) -> str:
    """
    Save markdown content to a file. Handles name collision by appending item_id suffix.
    """
    directory = os.path.dirname(filepath)
    os.makedirs(directory, exist_ok=True)
    
    final_filepath = filepath
    if os.path.exists(filepath):
        # File name collision - append suffix from item_id
        base, ext = os.path.splitext(filepath)
        suffix = str(item_id)[-6:] if item_id else "dup"
        final_filepath = f"{base}_{suffix}{ext}"
        logger.warning(f"File already exists. Saving collision to: {final_filepath}")
        
    with open(final_filepath, "w", encoding="utf-8") as f:
        f.write(content_str)
        
    logger.info(f"Saved file to: {final_filepath}")
    return final_filepath

class ManifestManager:
    """
    Manages the manifest.json file to support incremental syncs.
    """
    def __init__(self, manifest_path: str):
        self.manifest_path = manifest_path
        self.data = self.load()
        
    def load(self) -> dict:
        if not os.path.exists(self.manifest_path):
            logger.info("Manifest file does not exist, creating new structure.")
            return {
                "version": 1,
                "last_sync": "",
                "synced_items": {}
            }
        try:
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load manifest.json: {e}")
            return {
                "version": 1,
                "last_sync": "",
                "synced_items": {}
            }
            
    def save(self):
        directory = os.path.dirname(self.manifest_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        try:
            with open(self.manifest_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
            logger.debug("Successfully saved manifest.json.")
        except Exception as e:
            logger.error(f"Failed to save manifest.json: {e}")
            
    def is_synced(self, unique_key: str) -> bool:
        synced_items = self.data.get("synced_items", {})
        item = synced_items.get(unique_key, {})
        # If it exists and status is not 'removed', it's synced
        return unique_key in synced_items and item.get("status") != "removed"
        
    def add_item(self, unique_key: str, item_info: dict, tagging_status: str = "pending"):
        if "synced_items" not in self.data:
            self.data["synced_items"] = {}
        item_info["synced_at"] = datetime.now().isoformat()
        item_info["status"] = "synced"
        item_info["tagging_status"] = tagging_status
        item_info["tagged_at"] = None
        self.data["synced_items"][unique_key] = item_info
        self.data["last_sync"] = datetime.now().isoformat()
        self.save()
        
    def mark_removed(self, unique_key: str):
        synced_items = self.data.get("synced_items", {})
        if unique_key in synced_items:
            synced_items[unique_key]["status"] = "removed"
            synced_items[unique_key]["removed_at"] = datetime.now().isoformat()
            self.save()

    def update_tagging_status(self, unique_key: str, status: str):
        """
        Update tagging_status for an existing manifest item.
        status: one of "pending", "tagged", "failed", "skipped"
        """
        synced_items = self.data.get("synced_items", {})
        if unique_key in synced_items:
            synced_items[unique_key]["tagging_status"] = status
            if status == "tagged":
                synced_items[unique_key]["tagged_at"] = datetime.now().isoformat()
            self.save()

    def get_untagged_items(self) -> list:
        """
        Return list of (unique_key, item_dict) for all items with
        tagging_status in ("pending", "failed").
        """
        synced_items = self.data.get("synced_items", {})
        result = []
        for key, item in synced_items.items():
            ts = item.get("tagging_status", "pending")
            if ts in ("pending", "failed"):
                result.append((key, item))
        return result
            
    def get_stats(self) -> dict:
        synced_items = self.data.get("synced_items", {})
        active = sum(1 for item in synced_items.values() if item.get("status") == "synced")
        removed = sum(1 for item in synced_items.values() if item.get("status") == "removed")
        return {
            "total_active": active,
            "total_removed": removed,
            "last_sync": self.data.get("last_sync", "")
        }
