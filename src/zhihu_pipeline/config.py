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
class NotifyConfig:
    enabled: bool = True
    gateway_url: str = ""
    api_key: str = ""
    service: str = "ZhihuPipeline"
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
    video_enabled: bool = True
    video_quality: str = "ld"
    video_max_size_mb: float = 50.0

@dataclass
class OutputConfig:
    vault_path: str = "~/notes"
    collection_dir: str = "知乎收藏"
    image_naming: str = "file-${date:YYYYMMDDHHmmssSSS}"
    # Relative to vault_path. Empty keeps the legacy location
    # "{collection_dir}/manifest.json". Prefer a dot-directory: Obsidian does
    # not surface it, so neither the Git channel nor a LiveSync node can carry
    # the pipeline state file to another device and resurrect stale records.
    manifest_path: str = ""

    def __post_init__(self):
        self.vault_path = os.path.abspath(os.path.expanduser(self.vault_path))

@dataclass
class TaggerConfig:
    enabled: bool = False
    backend: str = "local"
    base_url: str = "http://localhost:11434/v1"
    model: str = "qwen2.5:3b"
    timeout: float = 600.0
    api_key: str = ""
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
    # "git": publish through GitHub. "livesync": a headless LiveSync node owns
    # delivery, so this pipeline must not touch GitHub at all (not even pull).
    # "none": nothing publishes; the pipeline has to say so instead of claiming
    # the notes were delivered.
    sync_mode: str = "git"
    repo_url: str = ""
    branch: str = "main"
    user_name: str = ""
    user_email: str = ""
    auto_pull: bool = True
    auto_push: bool = True

@dataclass
class LiveSyncConfig:
    """
    Read-only verification against the Self-hosted LiveSync CouchDB backend.

    This pipeline never writes to CouchDB itself: a separate headless LiveSync
    node mirrors the vault directory into the database. The pipeline only asks
    "did my files actually arrive?" before it may consume the Zhihu inbox, so a
    silent node must not be mistaken for a successful sync.
    """
    enabled: bool = False
    couchdb_url: str = ""          # e.g. http://127.0.0.1:5984 when on the same host
    db_name: str = "obsidian-vault"
    username: str = ""             # prefer LIVESYNC_COUCHDB_USER
    password: str = ""             # prefer LIVESYNC_COUCHDB_PASSWORD
    node_name: str = ""            # device_name written by the node (heartbeat)
    timeout: float = 10.0
    # How long to wait for the node to publish freshly written notes.
    verify_timeout_seconds: float = 90.0
    verify_poll_interval_seconds: float = 5.0
    # A node that has not touched the milestone document this long is dead,
    # and its silence must be reported as a failure rather than success.
    node_max_stale_minutes: float = 30.0
    # LiveSync can obfuscate document paths; then per-path existence checks are
    # meaningless and only the replication cursor is usable.
    use_path_obfuscation: bool = False

@dataclass
class Config:
    chrome: ChromeConfig = field(default_factory=ChromeConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    git: GitConfig = field(default_factory=GitConfig)
    livesync: LiveSyncConfig = field(default_factory=LiveSyncConfig)
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
    notify_data = data.get("notify") or {}
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
    notify = NotifyConfig(
        enabled=bool(notify_data.get("enabled", True)),
        gateway_url=str(os.environ.get("NOTIFY_GATEWAY_URL", notify_data.get("gateway_url", ""))),
        api_key=str(os.environ.get("NOTIFY_GATEWAY_KEY", notify_data.get("api_key", ""))),
        service=str(notify_data.get("service", "ZhihuPipeline")),
        timeout=int(notify_data.get("timeout", telegram_data.get("timeout", 300)))
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
        schedule_jitter_minutes=float(sync_data.get("schedule_jitter_minutes", 25.0)),
        video_enabled=bool(sync_data.get("video_enabled", True)),
        video_quality=str(sync_data.get("video_quality", "ld")).lower(),
        video_max_size_mb=float(sync_data.get("video_max_size_mb", 50.0))
    )
    if sync.video_quality not in {"ld", "sd", "hd", "fhd"}:
        logger.warning(f"Unsupported sync.video_quality={sync.video_quality!r}; using 'ld'.")
        sync.video_quality = "ld"
    output = OutputConfig(
        vault_path=os.environ.get("OUTPUT_VAULT_PATH", output_data.get("vault_path", "~/notes")),
        collection_dir=output_data.get("collection_dir", "知乎收藏"),
        image_naming=output_data.get("image_naming", "file-${date:YYYYMMDDHHmmssSSS}"),
        manifest_path=str(output_data.get("manifest_path", "") or "").strip()
    )
    tagger = TaggerConfig(
        enabled=tagger_data.get("enabled", False),
        backend=tagger_data.get("backend", "local"),
        base_url=tagger_data.get("base_url", "http://localhost:11434/v1"),
        model=tagger_data.get("model", "qwen2.5:3b"),
        timeout=int(tagger_data.get("timeout", 120)),
        api_key=str(os.environ.get("TAGGER_API_KEY", tagger_data.get("api_key", ""))),
        valid_domains=tagger_data.get("valid_domains", TaggerConfig().valid_domains)
    )

    git_data = data.get("git") or {}
    livesync_data = data.get("livesync") or {}

    # "false" is truthy in Python, so an env override must be parsed by value.
    def _as_bool(raw: Any, default: bool = False) -> bool:
        if isinstance(raw, bool):
            return raw
        if raw is None:
            return default
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}

    sync_mode = str(
        os.environ.get("GIT_SYNC_MODE", git_data.get("sync_mode", "git")) or "git"
    ).strip().lower()
    if sync_mode in {"livesync_node", "live-sync", "self_hosted_livesync"}:
        sync_mode = "livesync"
    if sync_mode not in {"git", "livesync", "none"}:
        # Falling back to "git" here would silently push private notes to
        # GitHub on a typo. "none" fails closed: nothing is published, nothing
        # is consumed from the inbox, and the run reports it loudly.
        logger.critical(
            f"Unknown git.sync_mode={sync_mode!r}; refusing to guess. "
            "Treating this run as sync_mode='none' (nothing will be published)."
        )
        sync_mode = "none"

    git = GitConfig(
        enabled=_as_bool(os.environ.get("GIT_ENABLED", git_data.get("enabled", False))),
        sync_mode=sync_mode,
        repo_url=str(os.environ.get("GIT_REPO_URL", git_data.get("repo_url", ""))),
        branch=str(os.environ.get("GIT_BRANCH", git_data.get("branch", "main"))),
        user_name=str(os.environ.get("GIT_USER_NAME", git_data.get("user_name", ""))),
        user_email=str(os.environ.get("GIT_USER_EMAIL", git_data.get("user_email", ""))),
        auto_pull=_as_bool(git_data.get("auto_pull", True), True),
        auto_push=_as_bool(git_data.get("auto_push", True), True)
    )

    livesync = LiveSyncConfig(
        enabled=_as_bool(os.environ.get("LIVESYNC_ENABLED", livesync_data.get("enabled", False))),
        couchdb_url=str(os.environ.get(
            "LIVESYNC_COUCHDB_URL", livesync_data.get("couchdb_url", "")
        )).rstrip("/"),
        db_name=str(os.environ.get("LIVESYNC_DB_NAME", livesync_data.get("db_name", "obsidian-vault"))),
        username=str(os.environ.get("LIVESYNC_COUCHDB_USER", livesync_data.get("username", ""))),
        password=str(os.environ.get("LIVESYNC_COUCHDB_PASSWORD", livesync_data.get("password", ""))),
        node_name=str(os.environ.get("LIVESYNC_NODE_NAME", livesync_data.get("node_name", ""))),
        timeout=float(livesync_data.get("timeout", 10.0)),
        verify_timeout_seconds=float(livesync_data.get("verify_timeout_seconds", 90.0)),
        verify_poll_interval_seconds=float(livesync_data.get("verify_poll_interval_seconds", 5.0)),
        node_max_stale_minutes=float(livesync_data.get("node_max_stale_minutes", 30.0)),
        use_path_obfuscation=_as_bool(livesync_data.get("use_path_obfuscation", False))
    )
    if sync_mode == "livesync" and not livesync.enabled:
        logger.warning(
            "git.sync_mode='livesync' but livesync.enabled=false: new notes will "
            "land on disk with no way for this pipeline to confirm delivery, so "
            "they will not be removed from the Zhihu inbox."
        )

    return Config(
        chrome=chrome,
        telegram=telegram,
        notify=notify,
        git=git,
        livesync=livesync,
        sync=sync,
        output=output,
        tagger=tagger,
        selectors=selectors_data
    )
