import asyncio
import os
import click
from loguru import logger

from zhihu_pipeline.config import load_config
from zhihu_pipeline.sync_engine import SyncEngine

@click.group()
def cli():
    """Zhihu collections to Obsidian Vault Sync Pipeline."""
    pass

@cli.command()
@click.option("--full", is_flag=True, help="Full sync, ignoring previous sync history.")
@click.option("--collection", default=None, help="Sync only a specific collection by title.")
def sync(full, collection):
    """Synchronize collections with the local Obsidian Vault."""
    config = load_config()
    engine = SyncEngine(config)
    
    # Run sync process
    asyncio.run(engine.run(full_sync=full, target_collection=collection))

@cli.command()
def status():
    """Show current sync stats from the manifest.json."""
    config = load_config()
    engine = SyncEngine(config)
    engine.show_status()

@cli.command("check-auth")
def check_auth():
    """Check connectivity and Zhihu login state."""
    config = load_config()
    engine = SyncEngine(config)
    asyncio.run(engine.check_auth())

@cli.command()
@click.option("--dry-run", is_flag=True, help="Only display pending files without invoking LM Studio.")
@click.option("--force", is_flag=True, help="Reprocess all files, including those already marked as tagged.")
def tag(dry_run, force):
    """
    Run auto-tagging for all untagged (pending/failed) articles.
    Can be run independently, fully decoupled from the sync command.
    """
    config = load_config()
    if not config.tagger.enabled:
        logger.warning("tagger.enabled is false. Please enable it in config.yaml first.")
        return

    from zhihu_pipeline.storage import ManifestManager
    from zhihu_pipeline.tagger import run_tagging_pass
    import os
    manifest_path = os.path.join(
        config.output.vault_path, config.output.collection_dir, "manifest.json"
    )
    manifest = ManifestManager(manifest_path)

    if force:
        # Reset all tagged status to pending
        for key, item in manifest.data.get("synced_items", {}).items():
            if item.get("tagging_status") == "tagged":
                item["tagging_status"] = "pending"
        manifest.save()
        logger.info("--force: Reset all 'tagged' records to 'pending'.")

    pending = manifest.get_untagged_items()
    logger.info(f"Found {len(pending)} articles pending for tagging.")

    if dry_run:
        for key, item in pending:
            print(f"  [pending] {item.get('title', key)}  ({item.get('local_path', '')})")
        return

    success, fail = run_tagging_pass(manifest, config.output.vault_path, config.tagger)
    logger.info(f"Tagging complete: {success} successful, {fail} failed.")
    if fail > 0:
        logger.info("Failed articles have been marked as 'failed' and will be retried next time the 'tag' command is run.")

@cli.command("clear-archive")
@click.option("--collection", default="archive", help="Collection title or numeric ID to clear (defaults to 'archive').")
def clear_archive(collection):
    """Batch remove all items from a Zhihu collection (defaults to 'archive')."""
    config = load_config()
    engine = SyncEngine(config)

    async def _run():
        context = await engine.get_browser_context()
        try:
            from zhihu_pipeline.auth import get_or_create_page, check_login
            page = await get_or_create_page(context)
            logged_in, username = await check_login(page)
            if not logged_in:
                logger.error("You must be logged in to Zhihu to clear collection contents.")
                return

            from zhihu_pipeline.cleaner import delete_collection
            result = await delete_collection(page, target_name_or_id=collection)
            print(f"\n=== 操作完成 ===")
            print(f"状态: {result.get('status')}")
            print(f"目标收藏夹: {result.get('collection_title')} (ID: {result.get('collection_id', 'N/A')})\n")
        finally:
            await context.close()

@cli.command()
def bot():
    """Run the scheduler with optional Telegram long-polling (legacy entrypoint)."""
    from zhihu_pipeline.bot import TelegramBotDaemon
    config = load_config()
    daemon = TelegramBotDaemon(config)
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(daemon.run_polling())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot daemon interrupted by user.")
    finally:
        daemon.stop()
        loop.close()


@cli.command("migrate-attachments")
@click.option("--vault", default=None, help="Vault path; defaults to output.vault_path from config.yaml")
@click.option("--apply", "do_apply", is_flag=True, help="Actually move directories and rewrite links (default: dry run)")
@click.option("--force", is_flag=True, help="Also move directories that notes outside the collection reference (breaks those links)")
def migrate_attachments_cmd(vault, do_apply, force):
    """
    Move Zhihu image attachments from assets/<note title>/ to assets/知乎附件/.

    Needed so a Self-hosted LiveSync node can be allow-listed by path without
    either dropping Zhihu images or pulling unrelated private attachments onto
    the NAS. Dry run by default; re-running after a migration is a no-op.
    """
    from zhihu_pipeline.images import ZHIHU_ATTACHMENT_ROOT
    from zhihu_pipeline.migrate_attachments import migrate

    config = load_config()
    vault_path = os.path.abspath(os.path.expanduser(vault)) if vault else config.output.vault_path
    logger.info(f"Migration target vault: {vault_path}")
    report = migrate(vault_path, config.output.collection_dir, apply=do_apply, force=force)

    print("\n" + report.summary())
    def _show(label, items, template):
        for name in items[:20]:
            print(f"  {label} {template.format(name=name)}")
        if len(items) > 20:
            print(f"  {label} ...另有 {len(items) - 20} 项")
    _show("[move]", report.moved, "assets/{name} -> " + ZHIHU_ATTACHMENT_ROOT + "/{name}")
    _show("[conflict]", report.conflicts, "assets/{name} 目标已存在，未移动")
    _show("[missing]", report.missing, "assets/{name} 被引用但磁盘上不存在")
    for name in sorted(report.external_refs)[:20]:
        holders = report.external_refs[name]
        print(f"  [held-back] assets/{name} 同时被 {len(holders)} 篇 collection 外笔记引用，例如 {holders[0]}")
    _show("[rewrite]", report.rewritten_notes, "{name}")
    if not do_apply and (report.moved or report.rewritten_notes):
        print("\n这是空跑结果。加 --apply 才会真正执行。")



@cli.command("check-attachments")
@click.option("--vault", default=None, help="Vault path; defaults to output.vault_path from config.yaml")
def check_attachments_cmd(vault):
    """
    Exit non-zero unless every referenced attachment exists under the dedicated prefix.

    Use this as a preflight before starting or refreshing a LiveSync node: a snapshot
    that still points at old attachment paths would publish broken notes over good ones.
    """
    import sys
    from zhihu_pipeline.migrate_attachments import audit_attachments

    config = load_config()
    vault_path = os.path.abspath(os.path.expanduser(vault)) if vault else config.output.vault_path
    audit = audit_attachments(vault_path, config.output.collection_dir)
    print(audit.summary())
    for name, notes in list(audit.stale_refs.items())[:10]:
        print(f"  [stale] assets/{name} 仍被 {len(notes)} 篇笔记引用，例如 {notes[0]}")
    for name in audit.missing_dirs[:10]:
        print(f"  [missing] assets/{name} 被引用但目录不存在")
    if len(audit.stale_refs) > 10 or len(audit.missing_dirs) > 10:
        print("  ...另有更多条目，用 migrate-attachments 查看完整列表")
    sys.exit(0 if audit.ok else 1)


@cli.command()
def worker():
    """Run scheduled sync only; notification delivery remains via notify-gateway."""
    from zhihu_pipeline.bot import TelegramBotDaemon
    config = load_config()
    daemon = TelegramBotDaemon(config)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(daemon.run_worker())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduled worker interrupted by user.")
    finally:
        daemon.stop()
        loop.close()

if __name__ == "__main__":
    cli()
