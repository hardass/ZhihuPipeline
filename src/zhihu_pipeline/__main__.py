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
        print("⚠️  tagger.enabled 为 false，请先在 config.yaml 中开启。")
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
        print("ℹ️  --force: 已将所有 tagged 记录重置为 pending。")

    pending = manifest.get_untagged_items()
    print(f"ℹ️  共发现 {len(pending)} 篇文章待打标签。")

    if dry_run:
        for key, item in pending:
            print(f"  [pending] {item.get('title', key)}  ({item.get('local_path', '')})")
        return

    success, fail = run_tagging_pass(manifest, config.output.vault_path, config.tagger)
    print(f"\n✅ 打标签完成：成功 {success} 篇，失败 {fail} 篇。")
    if fail > 0:
        print("ℹ️  失败的文章已标记为 failed，下次运行 `tag` 命令时将自动重试。")

if __name__ == "__main__":
    cli()
