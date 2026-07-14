import asyncio
import os
from zhihu_pipeline.auth import connect_chrome, get_or_create_page, check_login
from zhihu_pipeline.fetcher import fetch_collections, fetch_collection_items, fetch_content_detail
from zhihu_pipeline.config import load_config
from zhihu_pipeline.parser import html_to_markdown
from zhihu_pipeline.images import download_images

async def test():
    config = load_config()
    print("Loading config...")
    print(f"Chrome remote debugging port: {config.chrome.debug_port}")
    print(f"Vault path: {config.output.vault_path}")
    
    try:
        browser, context = await connect_chrome(config.chrome.debug_port)
    except Exception as e:
        print(f"\n[ERROR] Connection failed: {e}")
        return
        
    page = await get_or_create_page(context)
    logged_in, username = await check_login(page)
    
    print(f"\nLogin Status: {'Logged In' if logged_in else 'Not Logged In'}")
    if logged_in:
        print(f"Username: {username}")
        
        try:
            collections = await fetch_collections(page)
            if collections:
                # Test first collection item list fetching
                target = collections[0]
                print(f"\nFetching items for collection: {target['title']} (ID: {target['id']})...")
                items = await fetch_collection_items(page, target['id'])
                
                selected_item = None
                selected_detail = None
                
                # Look for an item that contains images first
                print("\nScanning items for content with images...")
                for item in items[:10]: # Check first 10 items for speed
                    print(f"Checking item: {item['title']}...")
                    detail = await fetch_content_detail(page, item, config.selectors)
                    html_content = detail.get("content_html", "")
                    
                    if len(html_content) > 100:
                        selected_item = item
                        selected_detail = detail
                        # Check if it has a zhihu image (excluding math formula images)
                        if 'zhimg.com' in html_content and 'class="ztext-math"' not in html_content:
                            print("Found an item with images!")
                            break
                
                if not selected_item and items:
                    # Fallback to the first item
                    selected_item = items[0]
                    selected_detail = await fetch_content_detail(page, selected_item, config.selectors)
                
                if selected_item and selected_detail:
                    html_content = selected_detail.get("content_html", "")
                    
                    # Step 4: Parse HTML to Markdown
                    print("\n--- Step 4: HTML to Markdown ---")
                    markdown_text = html_to_markdown(html_content)
                    print(f"Parsed Markdown length: {len(markdown_text)} chars")
                    print("\nFirst 300 characters of Markdown:")
                    print(markdown_text[:300])
                    print("---------------------------------")
                    
                    # Step 5: Download images
                    print("\n--- Step 5: Download Images ---")
                    note_name = selected_item["title"].replace("/", "_").replace("?", "") # simple sanitize
                    output_dir = config.output.vault_path
                    
                    updated_markdown = await download_images(markdown_text, note_name, output_dir)
                    print(f"Updated Markdown length: {len(updated_markdown)} chars")
                    
                    # Check local assets
                    assets_path = os.path.join(output_dir, "assets", note_name)
                    if os.path.exists(assets_path):
                        files = os.listdir(assets_path)
                        print(f"\nDownloaded files in {assets_path}:")
                        for f in files:
                            print(f"  - {f} ({os.path.getsize(os.path.join(assets_path, f))} bytes)")
                    else:
                        print(f"\nNo assets directory found at: {assets_path}")
        except Exception as e:
            print(f"[ERROR] Fetching/Parsing failed: {e}")
    else:
        print("\n[WARNING] Please login to Zhihu in your Chrome window first, then run this test again.")

if __name__ == "__main__":
    asyncio.run(test())
