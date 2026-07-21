import os
import re
import json
import yaml
import httpx
from loguru import logger

# Constants for schema constraints
VALID_DOMAINS = ["AI", "Product", "Engineering", "Career", "Finance", "Life", "Home", "Hobbies", "Psychology", "Parenting"]
VALID_LEVELS = ["beginner", "intermediate", "advanced"]

SYSTEM_PROMPT = (
    "You are a helpful assistant. You must respond ONLY with a valid JSON object "
    "matching the requested schema. Do not include markdown code block wrapper or "
    "any introductory/concluding text."
)

USER_PROMPT_TEMPLATE = """
Analyze the following article content. Group it into the new three-dimensional classification schema:
1. 'domain': Select 1 to 3 items from this list only: {domains}.
2. 'concept': Identify 2 to 5 specific technical concepts, tools, or methodologies mentioned in the article.
   Use English or standard Chinese tech terms, kebab-case (e.g. RAG, browser-agent, decision-tree, management-skills, stock-market).
3. 'level': Assess the complexity and depth of the article. Must be exactly one of these: {levels}.
   - 'beginner': For general introductions, news, or basic guides.
   - 'intermediate': For practical guides, reviews, code tutorials, or middle management.
   - 'advanced': For deep technical analysis, system architectures, deep research, or core theory.
4. 'summary': A brief one-sentence summary in Chinese.

Return JSON format matching the schema:
{{
  "domain": ["string"],
  "concept": ["string"],
  "level": "string",
  "summary": "string"
}}
"""

def call_lm_studio(content: str, cfg) -> dict:
    """
    Calls local LM Studio API endpoint via HTTPX.
    """
    url = f"{cfg.lm_studio_url.rstrip('/')}/v1/chat/completions"
    
    user_prompt = USER_PROMPT_TEMPLATE.format(
        domains=json.dumps(VALID_DOMAINS),
        levels=json.dumps(VALID_LEVELS),
        content=content[:6000]
    )
    
    payload = {
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ]
    }
    
    # We execute a synchronous request via HTTPX client.
    # trust_env=False prevents HTTPX from using local system proxies for localhost requests.
    with httpx.Client(timeout=cfg.timeout, trust_env=False) as client:
        response = client.post(url, json=payload)
        response.raise_for_status()
        res_data = response.json()
        
    text = res_data["choices"][0]["message"]["content"].strip()
    # Clean up code blocks if model added them despite system instructions
    clean_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    result = json.loads(clean_text)
    
    # Validate and fallback domain list
    domains = [d for d in result.get("domain", []) if d in VALID_DOMAINS]
    if not domains:
        domains = ["Life"]
        
    # Validate level
    level = result.get("level", "intermediate")
    if level not in VALID_LEVELS:
        level = "intermediate"
        
    return {
        "domain": domains,
        "concept": result.get("concept", []),
        "level": level,
        "summary": result.get("summary", "")
    }

def tag_single_file(file_path: str, cfg) -> bool:
    """
    Reads a Markdown file, updates its YAML frontmatter with LLM tagging, and writes it back.
    """
    if not os.path.exists(file_path):
        logger.warning(f"File not found for tagging: {file_path}")
        return False

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
        
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if not fm_match:
        logger.warning(f"No YAML frontmatter found in file: {file_path}")
        return False
        
    try:
        frontmatter = yaml.safe_load(fm_match.group(1)) or {}
        body = content[fm_match.end():]
    except Exception as e:
        logger.error(f"Failed to parse YAML frontmatter in {file_path}: {e}")
        return False
        
    # Idempotency check: if we already have the metrics and not forced, skip LLM
    if "domain" in frontmatter and "concept" in frontmatter and "level" in frontmatter:
        logger.info(f"File is already tagged, skipping: {os.path.basename(file_path)}")
        return True
        
    # Call Local LLM
    try:
        result = call_lm_studio(body, cfg)
        
        # Inject classifications
        frontmatter["domain"] = result["domain"]
        frontmatter["concept"] = result["concept"]
        frontmatter["level"] = result["level"]
        frontmatter["summary"] = result["summary"]
        
        # Strip legacy tags if any to avoid pollution
        tags = frontmatter.get("tags", [])
        if isinstance(tags, list):
            frontmatter["tags"] = [t for t in tags if t not in ["clippings", "zhihu"]]
            if not frontmatter["tags"]:
                frontmatter["tags"] = ["zhihu"]
                
        # Write back to file
        fm_str = yaml.safe_dump(frontmatter, allow_unicode=True, default_flow_style=False, sort_keys=False)
        new_content = f"---\n{fm_str}---\n\n{body.lstrip()}"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(new_content)
            
        logger.info(f"Successfully tagged file: {os.path.basename(file_path)}")
        return True
        
    except Exception as e:
        logger.error(f"Error tagging file {os.path.basename(file_path)}: {e}")
        return False

def run_tagging_pass(manifest, vault_path: str, cfg) -> tuple:
    """
    Finds all pending or failed files in the manifest and attempts to tag them.
    Returns (success_count, fail_count).
    """
    pending_items = manifest.get_untagged_items()
    if not pending_items:
        logger.info("No pending or failed files found for tagging.")
        return 0, 0
        
    logger.info(f"Starting tagging pass for {len(pending_items)} files...")
    
    success_count = 0
    fail_count = 0
    
    for key, item in pending_items:
        local_path = item.get("local_path")
        if not local_path:
            logger.warning(f"Item {key} has no local_path specified in manifest. Skipping.")
            manifest.update_tagging_status(key, "failed")
            fail_count += 1
            continue
            
        full_path = os.path.join(vault_path, local_path)
        
        # Attempt tagging
        success = tag_single_file(full_path, cfg)
        if success:
            manifest.update_tagging_status(key, "tagged")
            success_count += 1
        else:
            manifest.update_tagging_status(key, "failed")
            fail_count += 1
            
    return success_count, fail_count
