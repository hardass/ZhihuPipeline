import os
import re
import json
import yaml
import httpx
from loguru import logger

# ============================================================
# Schema Constants
# ============================================================
VALID_DOMAINS = ["AI", "Product", "Engineering", "Career", "Finance", "Life", "Home", "Hobbies", "Psychology", "Parenting"]
VALID_LEVELS = ["beginner", "intermediate", "advanced"]

# ============================================================
# Layer 1: Prompt Constraint
# ============================================================
SYSTEM_PROMPT = (
    "You are a knowledge classification assistant. "
    "You MUST respond ONLY with a valid JSON object matching the requested schema. "
    "Do NOT include markdown code block wrappers (```json ... ```) or any other text. "
    "STRICT FORMATTING RULES for the 'concept' field: "
    "(1) Acronyms must be ALL-CAPS: RAG, LLM, MCP, NLP, API, GPU, NFC, TDD, SFT, PRD, BM25, BGE, VIX. "
    "(2) Proper nouns must use standard capitalization: Python, Docker, Linux, ChatGPT, Claude, DeepSeek, Gemini, Cloudflare, Raspberry-Pi. "
    "(3) All other concepts must be lowercase kebab-case: vibe-coding, prompt-engineering, decision-tree, machine-learning. "
    "(4) NEVER include spaces inside a concept term (use hyphens instead). "
    "(5) NEVER output: zhihu, clippings, article, null, undefined, or any punctuation."
)

USER_PROMPT_TEMPLATE = """\
Analyze the following article content and classify it using this three-dimensional schema:

1. 'domain': Select 1 to 3 items from ONLY this list: {domains}.
2. 'concept': Identify 2 to 5 specific technical concepts, tools, or methodologies actually present in the article.
   Use kebab-case. Remember: acronyms ALL-CAPS, proper nouns PascalCase, everything else lowercase-kebab.
3. 'level': Assess complexity. Must be exactly one of: {levels}.
   - 'beginner': General introduction, news, basic how-to, or lifestyle.
   - 'intermediate': Practical guide, code tutorial, review, or mid-level management.
   - 'advanced': Deep technical analysis, system architecture, core theory, or original research.
4. 'summary': A single sentence summary in Chinese (30-60 characters).

Return ONLY this JSON structure:
{{
  "domain": ["string"],
  "concept": ["string"],
  "level": "string",
  "summary": "string"
}}

Article content:
{content}
"""


# ============================================================
# Layer 2: Post-Processing Sanitizer
# ============================================================

# Acronyms: force ALL-CAPS
ACRONYMS = {
    "rag": "RAG", "llm": "LLM", "mcp": "MCP", "nlp": "NLP", "prd": "PRD",
    "mrd": "MRD", "brd": "BRD", "sft": "SFT", "tdd": "TDD", "rdf": "RDF",
    "api": "API", "mbti": "MBTI", "tpu": "TPU", "gpu": "GPU", "nfc": "NFC",
    "vix": "VIX", "kvm": "KVM", "bge": "BGE", "bge-m3": "BGE-M3", "bm25": "BM25",
    "us-stocks": "US-stocks", "w-8ben": "W-8BEN", "sql": "SQL", "orm": "ORM",
    "dsl": "DSL", "etl": "ETL", "rag": "RAG", "grpc": "gRPC", "http": "HTTP",
    "https": "HTTPS", "jwt": "JWT", "oauth": "OAuth", "oop": "OOP", "fp": "FP",
    "ui": "UI", "ux": "UX", "pm": "PM",
}

# Proper nouns: force standard capitalization
PROPER_NOUNS = {
    "docker": "Docker", "python": "Python", "chatgpt": "ChatGPT",
    "claude": "Claude", "gemini": "Gemini", "deepseek": "DeepSeek",
    "cloudflare": "Cloudflare", "linux": "Linux", "raspberry-pi": "Raspberry-Pi",
    "rsync": "rsync", "github": "GitHub", "gitlab": "GitLab", "postgres": "Postgres",
    "postgresql": "PostgreSQL", "mysql": "MySQL", "redis": "Redis",
    "elasticsearch": "Elasticsearch", "mongodb": "MongoDB", "kubernetes": "Kubernetes",
    "obsidian": "Obsidian", "notion": "Notion", "langchain": "LangChain",
    "llamaindex": "LlamaIndex", "openai": "OpenAI", "anthropic": "Anthropic",
    "huggingface": "HuggingFace", "tensorflow": "TensorFlow", "pytorch": "PyTorch",
    "react": "React", "nextjs": "Next.js", "vuejs": "Vue.js", "fastapi": "FastAPI",
    "typescript": "TypeScript", "javascript": "JavaScript", "swift": "Swift",
    "golang": "Go", "rust": "Rust",
}

# Garbage tag blacklist: these are NEVER useful for knowledge retrieval
BLACKLIST = {"zhihu", "clippings", "article", "undefined", "null", "none", "tag", "tags"}


def sanitize_term(term: str) -> str:
    """Sanitize a single concept or domain tag from LLM output."""
    if not isinstance(term, str):
        return ""

    # Strip surrounding whitespace, quotes, commas, full stops (half/full-width)
    term = term.strip().strip("'\"").strip(",").strip(".").strip("，").strip("。").strip()
    if not term:
        return ""

    term_lower = term.lower()

    # Filter blacklist
    if term_lower in BLACKLIST:
        return ""

    # Acronym lookup (case-insensitive exact match)
    if term_lower in ACRONYMS:
        return ACRONYMS[term_lower]

    # Proper noun lookup (case-insensitive exact match)
    if term_lower in PROPER_NOUNS:
        return PROPER_NOUNS[term_lower]

    # Normalize internal spaces → hyphens, then force lowercase
    normalized = re.sub(r"\s+", "-", term_lower)

    return normalized


def sanitize_metadata(metadata: dict) -> dict:
    """
    Run sanitize_term over all list fields (domain, concept, tags).
    Deduplicates while preserving order.
    """
    for key in ["domain", "concept", "tags"]:
        if key in metadata and isinstance(metadata[key], list):
            cleaned = []
            seen = set()
            for item in metadata[key]:
                clean = sanitize_term(str(item))
                if clean and clean not in seen:
                    cleaned.append(clean)
                    seen.add(clean)
            metadata[key] = cleaned
    return metadata


# ============================================================
# Layer 3: Body Code Block Fencing Check
# ============================================================

def ensure_code_blocks_fenced(body_text: str) -> str:
    """
    Normalize code fences in article body to prevent bare '#' Python comments
    from leaking into Obsidian as tags.

    Actions:
    1. Normalize ```python3 → ```python (Obsidian syntax highlighting)
    2. Normalize ```Python / ```PYTHON → ```python (case-insensitive)
    3. Strip trailing whitespace inside code blocks (cosmetic)
    """
    # Normalize python3/Python/PYTHON variants
    body_text = re.sub(r"```[Pp][Yy][Tt][Hh][Oo][Nn]3?", "```python", body_text)

    # Normalize ```js/```JS → ```javascript for consistency
    body_text = re.sub(r"```[Jj][Ss]\b", "```javascript", body_text)

    return body_text


# ============================================================
# LM Studio API Call
# ============================================================

def call_lm_studio(content: str, cfg) -> dict:
    """
    Calls local LM Studio API via HTTPX.
    Returns a validated, sanitized metadata dict.
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

    # trust_env=False prevents HTTPX from using local system proxies for localhost requests.
    with httpx.Client(timeout=cfg.timeout, trust_env=False) as client:
        response = client.post(url, json=payload)
        response.raise_for_status()
        res_data = response.json()

    text = res_data["choices"][0]["message"]["content"].strip()
    # Clean up code block wrappers if model added them despite system instructions
    clean_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    raw_result = json.loads(clean_text)

    # --- Layer 2: Apply sanitizer ---
    raw_result = sanitize_metadata(raw_result)

    # Validate domain list against enum
    domains = [d for d in raw_result.get("domain", []) if d in VALID_DOMAINS]
    if not domains:
        domains = ["Life"]

    # Validate level against enum
    level = raw_result.get("level", "intermediate")
    if level not in VALID_LEVELS:
        level = "intermediate"

    return {
        "domain": domains,
        "concept": raw_result.get("concept", []),
        "level": level,
        "summary": raw_result.get("summary", "")
    }


# ============================================================
# File-level Tagging
# ============================================================

def tag_single_file(file_path: str, cfg) -> bool:
    """
    Reads a Markdown file, updates its YAML frontmatter with LLM tagging,
    applies all three guardrail layers, and writes back to disk.
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

    # Idempotency check: skip LLM if already fully tagged
    if "domain" in frontmatter and "concept" in frontmatter and "level" in frontmatter:
        logger.info(f"File is already tagged, skipping: {os.path.basename(file_path)}")
        return True

    # --- Layer 3: Fence code blocks in body before writing ---
    body = ensure_code_blocks_fenced(body)

    # Call Local LLM
    try:
        result = call_lm_studio(body, cfg)

        # Inject classifications (already sanitized inside call_lm_studio)
        frontmatter["domain"] = result["domain"]
        frontmatter["concept"] = result["concept"]
        frontmatter["level"] = result["level"]
        frontmatter["summary"] = result["summary"]

        # Sync concept terms into frontmatter['tags'] for Obsidian tag tree & graph view compatibility
        existing_tags = frontmatter.get("tags", [])
        if not isinstance(existing_tags, list):
            existing_tags = []

        # Keep non-blacklisted existing tags (e.g. zhihu) and merge sanitized concepts into tags
        cleaned_existing = [t for t in existing_tags if t not in BLACKLIST and t.strip()]
        if "zhihu" not in cleaned_existing:
            cleaned_existing = ["zhihu"] + cleaned_existing

        # Combine 'zhihu' + concepts into tags (preserving order and uniqueness)
        merged_tags = list(cleaned_existing)
        for c_term in result["concept"]:
            if c_term and c_term not in merged_tags and c_term.lower() not in BLACKLIST:
                merged_tags.append(c_term)

        frontmatter["tags"] = merged_tags

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


# ============================================================
# Batch Tagging Pass
# ============================================================

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

        success = tag_single_file(full_path, cfg)
        if success:
            manifest.update_tagging_status(key, "tagged")
            success_count += 1
        else:
            manifest.update_tagging_status(key, "failed")
            fail_count += 1

    return success_count, fail_count
