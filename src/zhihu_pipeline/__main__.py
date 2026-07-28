import asyncio
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
@click.option("--dry-run", is_flag=True, help="只显示待处理文件，不实际调用 LM Studio。")
@click.option("--force", is_flag=True, help="重新处理所有文件，包括已标记为 tagged 的。")
def tag(dry_run, force):
    """
    对所有未打标签（pending/failed）的文章执行打标签。
    可独立运行，与 sync 命令完全解耦。
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
        # 将所有 tagged 状态重置为 pending
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

if __name__ == "__main__":
    cli()
