"""
One-off migration: move Zhihu image attachments under a dedicated path prefix.

Background
----------
``images.py`` used to write attachments to ``assets/<note title>/``. That
namespace is shared with the attachments of private notes in the same vault, and
a Self-hosted LiveSync node can only be restricted by path - with the rule
applying in both directions. So a Zhihu node either had to ignore ``assets/``
entirely (Zhihu images never reach the phone) or allow it (private attachments
get pulled onto the NAS). This migration moves the Zhihu-owned subset to
``assets/知乎附件/`` and rewrites the links, after which the node's allow-list can
be tight:

    ^知乎收藏/|^assets/知乎视频/|^assets/知乎附件/

Safety properties
-----------------
* dry run by default; nothing changes without ``apply=True``;
* only directories that are actually referenced by a note under the collection
  directory are considered - unrelated entries in ``assets/`` are never touched;
* a name that already exists at the destination is reported as a conflict and
  left alone rather than merged or overwritten;
* links are decoded to find real directory names, but rewritten links keep the
  same encoding convention the pipeline uses (only spaces become ``%20``);
* note files are replaced atomically, and a second run is a no-op.
"""

import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger

from .images import ZHIHU_ATTACHMENT_ROOT

# ![alt](../../assets/<dir>/<file>)
LINK_RE = re.compile(r'(!\[[^\]]*\]\()\.\./\.\./assets/([^/)]+)/([^)]+)(\))')

PROTECTED_DIRS = {"知乎附件", "知乎视频"}


@dataclass
class MigrationReport:
    apply: bool = False
    referenced_dirs: list[str] = field(default_factory=list)
    moved: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    # Directories that notes outside the collection also reference. Moving them
    # would break links this migration cannot rewrite, so they are held back.
    external_refs: dict[str, list[str]] = field(default_factory=dict)
    rewritten_notes: list[str] = field(default_factory=list)

    @property
    def changed_dirs(self) -> list[str]:
        return sorted(set(self.moved))

    def summary(self) -> str:
        mode = "APPLY" if self.apply else "DRY-RUN"
        return (
            f"[{mode}] 被知乎笔记引用的附件目录 {len(self.referenced_dirs)} 个；"
            f"待迁移 {len(self.changed_dirs)}；目标已存在冲突 {len(self.conflicts)}；"
            f"被 collection 外笔记引用而暂留 {len(self.external_refs)}；"
            f"引用了但磁盘上没有 {len(self.missing)}；"
            f"{'已' if self.apply else '需'}重写笔记 {len(self.rewritten_notes)} 篇"
        )


def _decode(name: str) -> str:
    """Undo the only encoding the pipeline applies to links: %20 -> space."""
    return name.replace("%20", " ")


def _encode(name: str) -> str:
    return name.replace(" ", "%20")


def scan_links(vault_path: str, collection_dir: str) -> tuple[dict[str, set[str]], list[str]]:
    """
    Return ({assets directory name: [note paths referencing it]}, [note paths]).

    Scans every Markdown file below the collection directory and collects the
    ``../../assets/<name>/<file>`` links that still point outside the dedicated
    Zhihu prefixes.
    """
    root = os.path.join(vault_path, collection_dir)
    if not os.path.isdir(root):
        raise NotADirectoryError(f"collection directory not found: {root}")

    by_dir: dict[str, set[str]] = {}
    notes: list[str] = []
    for dirpath, _dirs, files in os.walk(root):
        for fname in files:
            if not fname.lower().endswith(".md"):
                continue
            note_path = os.path.join(dirpath, fname)
            notes.append(note_path)
            try:
                with open(note_path, "r", encoding="utf-8") as handle:
                    text = handle.read()
            except (OSError, UnicodeDecodeError) as exc:
                logger.warning(f"Cannot read note {note_path}: {exc}")
                continue
            for _prefix, raw_dir, _file, _suffix in LINK_RE.findall(text):
                name = _decode(raw_dir)
                if name in PROTECTED_DIRS or name.startswith("."):
                    continue
                by_dir.setdefault(name, set()).add(os.path.relpath(note_path, vault_path))
    return by_dir, notes


def rewrite_note(vault_path: str, note_rel_path: str, moved_names: set[str], apply: bool) -> bool:
    """Rewrite one note's links. Returns True when the file content would change."""
    note_path = os.path.join(vault_path, note_rel_path)
    try:
        with open(note_path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning(f"Cannot read note {note_path}: {exc}")
        return False

    def _sub(match: re.Match) -> str:
        prefix, raw_dir, fname, suffix = match.groups()
        if _decode(raw_dir) not in moved_names:
            return match.group(0)
        return f"{prefix}../../{ZHIHU_ATTACHMENT_ROOT}/{raw_dir}/{fname}{suffix}"

    new_text = LINK_RE.sub(_sub, text)
    if new_text == text:
        return False
    if apply:
        _atomic_write(note_path, new_text)
    return True


def _atomic_write(path: str, content: str) -> None:
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".migrate-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _safe_child(parent: str, name: str) -> Optional[str]:
    """Resolve name inside parent, refusing anything that escapes it."""
    candidate = os.path.normpath(os.path.join(parent, name))
    if not candidate.startswith(os.path.normpath(parent) + os.sep):
        logger.error(f"Refusing to touch path outside the assets directory: {name!r}")
        return None
    return candidate


# Any reference to assets/<dir>/ in a note, in either markdown-link or wikilink form.
ASSET_REF_RE = re.compile(r"assets/([^/\[\]()]+?)/")


def find_external_references(vault_path: str, collection_dir: str, names) -> dict[str, list[str]]:
    """
    Find notes *outside* the Zhihu collection that also reference these asset dirs.

    The migration can only rewrite links inside the collection. If a private note
    embeds one of the same attachments, moving it would silently break that note,
    so the directory has to be held back and reported instead.
    """
    wanted = {_decode(name): name for name in names}
    collection_root = os.path.join(vault_path, collection_dir) + os.sep
    found: dict[str, list[str]] = {}

    for dirpath, dirnames, filenames in os.walk(vault_path):
        # Never descend into the git store, editor state, or the attachment tree
        # itself: none of them contain notes, and .git alone is gigabytes.
        dirnames[:] = [d for d in dirnames if d not in {".git", ".obsidian", "assets", "node_modules"}]
        if dirpath.startswith(collection_root):
            continue
        for fname in filenames:
            if not fname.lower().endswith(".md"):
                continue
            note_path = os.path.join(dirpath, fname)
            try:
                with open(note_path, "r", encoding="utf-8") as handle:
                    text = handle.read()
            except (OSError, UnicodeDecodeError):
                continue
            for raw in ASSET_REF_RE.findall(text):
                original = wanted.get(_decode(raw))
                if original is None:
                    continue
                rel = os.path.relpath(note_path, vault_path)
                bucket = found.setdefault(original, [])
                if rel not in bucket:
                    bucket.append(rel)
    return found


def migrate(vault_path: str, collection_dir: str = "知乎收藏", apply: bool = False,
          force: bool = False) -> MigrationReport:
    """
    Move Zhihu-referenced attachment directories under ``assets/知乎附件/``.

    Without ``apply`` this only reports what would happen.
    """
    report = MigrationReport(apply=apply)
    assets_root = os.path.join(vault_path, "assets")
    destination_root = os.path.join(vault_path, *ZHIHU_ATTACHMENT_ROOT.split("/"))

    by_dir, _notes = scan_links(vault_path, collection_dir)
    report.referenced_dirs = sorted(by_dir)
    if not by_dir:
        logger.info("没有笔记仍引用旧附件路径，无需迁移。")
        return report

    if not os.path.isdir(assets_root):
        logger.warning(f"assets 目录不存在：{assets_root}")
        return report

    external = find_external_references(vault_path, collection_dir, by_dir.keys())
    if external and not force:
        logger.warning(
            f"{len(external)} 个附件目录同时被 collection 之外的笔记引用，本次暂不迁移："
            f"{', '.join(sorted(external)[:5])}{' ...' if len(external) > 5 else ''}"
        )
        report.external_refs = external
        by_dir = {name: notes for name, notes in by_dir.items() if name not in external}
        if not by_dir:
            logger.info(report.summary())
            return report
    elif external:
        logger.warning(
            f"--force 已指定：{len(external)} 个目录仍被 collection 外笔记引用，"
            "迁移后那些笔记的图片链接会失效。"
        )

    if apply:
        os.makedirs(destination_root, exist_ok=True)

    for name in sorted(by_dir):
        source = _safe_child(assets_root, name)
        target = _safe_child(destination_root, name)
        if source is None or target is None:
            report.conflicts.append(name)
            continue
        if not os.path.isdir(source):
            report.missing.append(name)
            continue
        if os.path.exists(target):
            logger.warning(f"目标已存在，跳过以免覆盖内容：{target}")
            report.conflicts.append(name)
            continue
        if apply:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.move(source, target)
        report.moved.append(name)

    moved_names = set(report.moved)
    if not moved_names:
        logger.info(report.summary())
        return report

    # Rewrite the links of every note that referenced a directory we moved.
    notes_to_touch: set[str] = set()
    for name in moved_names:
        notes_to_touch.update(by_dir.get(name, set()))
    for note_rel in sorted(notes_to_touch):
        if rewrite_note(vault_path, note_rel, moved_names, apply):
            report.rewritten_notes.append(note_rel)

    logger.info(report.summary())
    if not apply:
        logger.info("这是空跑结果。确认无误后加 --apply 才会真正移动与重写。")
    return report
