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

if __name__ == "__main__":
    cli()
