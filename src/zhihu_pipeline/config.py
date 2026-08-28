import os
from dataclasses import dataclass, field
from typing import List, Union, Dict, Any
import yaml
from loguru import logger

@dataclass
class ChromeConfig:
    debug_port: int = 9222
    user_data_dir: str = "~/.zhihu_pipeline/chrome_profile"
    headless: bool = False

    def __post_init__(self):
        self.user_data_dir = os.path.abspath(os.path.expanduser(self.user_data_dir))

@dataclass
class TelegramConfig:
    enabled: bool = True
    bot_token: str = ""
    chat_id: str = ""
    timeout: int = 300  # QR scan wait timeout (seconds)

@dataclass
class SyncConfig:
    collections: Union[str, List[str]] = "all"
    include_comments: bool = True
    max_comments: int = 20
    delay_min: float = 3.0
    delay_max: float = 8.0
    remove_after_sync: bool = True  # Automatically remove item from collection after successful local download
    auto_archive: bool = False      # Deprecated: kept for backward compatibility
    archive_name: str = "archive"   # Deprecated: kept for backward compatibility
    schedule_enabled: bool = True
    schedule_interval_hours: float = 2.0
    schedule_jitter_minutes: float = 25.0

@dataclass
class OutputConfig:
    vault_path: str = "~/notes"
    collection_dir: str = "知乎收藏"
    image_naming: str = "file-${date:YYYYMMDDHHmmssSSS}"

    def __post_init__(self):
        self.vault_path = os.path.abspath(os.path.expanduser(self.vault_path))

@dataclass
class TaggerConfig:
    enabled: bool = False
    backend: str = "local"
    lm_studio_url: str = "http://localhost:1234"
    model: str = "qwen2.5-3b-instruct-mlx"
    timeout: int = 120
    valid_domains: List[str] = field(default_factory=lambda: [
        "AI", "Product", "Engineering", "Career", "Finance",
        "Life", "Home", "Hobbies", "Psychology", "Parenting"
    ])

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
class GitConfig:
    enabled: bool = False
    repo_url: str = ""
    branch: str = "main"
    user_name: str = "hardass"
    user_email: str = "hardas.yang@gmail.com"
    auto_pull: bool = True
    auto_push: bool = True

@dataclass
class Config:
    chrome: ChromeConfig = field(default_factory=ChromeConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    git: GitConfig = field(default_factory=GitConfig)
    sync: SyncConfig = field(default_factory=SyncConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    tagger: TaggerConfig = field(default_factory=TaggerConfig)
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
    telegram_data = data.get("telegram") or {}
    sync_data = data.get("sync") or {}
    output_data = data.get("output") or {}
    tagger_data = data.get("tagger") or {}
    selectors_data = data.get("selectors") or {}

    chrome = ChromeConfig(
        debug_port=int(os.environ.get("CHROME_DEBUG_PORT", chrome_data.get("debug_port", 9222))),
        user_data_dir=os.environ.get("CHROME_USER_DATA_DIR", chrome_data.get("user_data_dir", "~/.zhihu_pipeline/chrome_profile")),
        headless=bool(chrome_data.get("headless", False))
    )
    telegram = TelegramConfig(
        enabled=bool(telegram_data.get("enabled", True)),
        bot_token=str(os.environ.get("TELEGRAM_BOT_TOKEN", telegram_data.get("bot_token", ""))),
        chat_id=str(os.environ.get("TELEGRAM_CHAT_ID", telegram_data.get("chat_id", ""))),
        timeout=int(telegram_data.get("timeout", 300))
    )
    sync = SyncConfig(
        collections=sync_data.get("collections", "all"),
        include_comments=sync_data.get("include_comments", True),
        max_comments=sync_data.get("max_comments", 20),
        delay_min=float(sync_data.get("delay_min", 3.0)),
        delay_max=float(sync_data.get("delay_max", 8.0)),
        remove_after_sync=sync_data.get("remove_after_sync", True),
        auto_archive=sync_data.get("auto_archive", False),
        archive_name=sync_data.get("archive_name", "archive"),
        schedule_enabled=bool(sync_data.get("schedule_enabled", True)),
        schedule_interval_hours=float(sync_data.get("schedule_interval_hours", 2.0)),
        schedule_jitter_minutes=float(sync_data.get("schedule_jitter_minutes", 25.0))
    )
    output = OutputConfig(
        vault_path=os.environ.get("OUTPUT_VAULT_PATH", output_data.get("vault_path", "~/notes")),
        collection_dir=output_data.get("collection_dir", "知乎收藏"),
        image_naming=output_data.get("image_naming", "file-${date:YYYYMMDDHHmmssSSS}")
    )
    tagger = TaggerConfig(
        enabled=tagger_data.get("enabled", False),
        backend=tagger_data.get("backend", "local"),
        lm_studio_url=tagger_data.get("lm_studio_url", "http://localhost:1234"),
        model=tagger_data.get("model", "qwen2.5-3b-instruct-mlx"),
        timeout=int(tagger_data.get("timeout", 120)),
        valid_domains=tagger_data.get("valid_domains", TaggerConfig().valid_domains)
    )

    git_data = data.get("git") or {}
    git = GitConfig(
        enabled=bool(os.environ.get("GIT_ENABLED", git_data.get("enabled", False))),
        repo_url=str(os.environ.get("GIT_REPO_URL", git_data.get("repo_url", ""))),
        branch=str(os.environ.get("GIT_BRANCH", git_data.get("branch", "main"))),
        user_name=str(os.environ.get("GIT_USER_NAME", git_data.get("user_name", "hardass"))),
        user_email=str(os.environ.get("GIT_USER_EMAIL", git_data.get("user_email", "hardas.yang@gmail.com"))),
        auto_pull=bool(git_data.get("auto_pull", True)),
        auto_push=bool(git_data.get("auto_push", True))
    )

    return Config(
        chrome=chrome,
        telegram=telegram,
        git=git,
        sync=sync,
        output=output,
        tagger=tagger,
        selectors=selectors_data
    )
