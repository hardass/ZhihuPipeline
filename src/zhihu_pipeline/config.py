import os
from dataclasses import dataclass, field
from typing import List, Union, Dict, Any
import yaml
from loguru import logger

@dataclass
class ChromeConfig:
    debug_port: int = 9222

@dataclass
class SyncConfig:
    collections: Union[str, List[str]] = "all"
    include_comments: bool = True
    max_comments: int = 20
    delay_min: float = 3.0
    delay_max: float = 8.0

@dataclass
class OutputConfig:
    vault_path: str = "~/notes"
    collection_dir: str = "知乎收藏"
    image_naming: str = "file-${date:YYYYMMDDHHmmssSSS}"

    def __post_init__(self):
        self.vault_path = os.path.abspath(os.path.expanduser(self.vault_path))

@dataclass
class SelectorConfig:
    question_title: str
    content: str
    author: str
    vote_count: str = ""
    time: str = ""

@dataclass
class SelectorsConfig:
    answer: SelectorConfig = field(default_factory=lambda: SelectorConfig(
        question_title="h1.QuestionHeader-title",
        content="div.RichContent-inner",
        author="div.AuthorInfo meta[itemprop='name']",
        vote_count="button.VoteButton--up",
        time="div.ContentItem-time"
    ))
    article: SelectorConfig = field(default_factory=lambda: SelectorConfig(
        title="h1.Post-Title",  # Note: DESIGN.md uses 'title' for articles, not question_title
        content="div.Post-RichTextContainer",
        author="div.AuthorInfo meta[itemprop='name']",
        time="div.ContentItem-time"
    ))

@dataclass
class Config:
    chrome: ChromeConfig = field(default_factory=ChromeConfig)
    sync: SyncConfig = field(default_factory=SyncConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    selectors: Dict[str, Any] = field(default_factory=dict)

def load_config(config_path: str = "config.yaml") -> Config:
    if not os.path.exists(config_path):
        logger.warning(f"Config file not found at {config_path}. Using default values.")
        return Config()

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        logger.error(f"Failed to read config file {config_path}: {e}. Using defaults.")
        data = {}

    chrome_data = data.get("chrome") or {}
    sync_data = data.get("sync") or {}
    output_data = data.get("output") or {}
    selectors_data = data.get("selectors") or {}

    chrome = ChromeConfig(
        debug_port=chrome_data.get("debug_port", 9222)
    )
    sync = SyncConfig(
        collections=sync_data.get("collections", "all"),
        include_comments=sync_data.get("include_comments", True),
        max_comments=sync_data.get("max_comments", 20),
        delay_min=float(sync_data.get("delay_min", 3.0)),
        delay_max=float(sync_data.get("delay_max", 8.0))
    )
    output = OutputConfig(
        vault_path=output_data.get("vault_path", "~/notes"),
        collection_dir=output_data.get("collection_dir", "知乎收藏"),
        image_naming=output_data.get("image_naming", "file-${date:YYYYMMDDHHmmssSSS}")
    )

    return Config(
        chrome=chrome,
        sync=sync,
        output=output,
        selectors=selectors_data
    )
