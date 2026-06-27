import asyncio
import json
import logging
import os
import sqlite3
import sys
import yaml
import argparse
import subprocess
import re
import importlib
from datetime import datetime
from pathlib import Path
from tenacity import retry, stop_after_attempt, wait_exponential

import pydantic

# Setup paths
CPO_CHAIN_DIR = Path(__file__).resolve().parent
BASE_DIR = CPO_CHAIN_DIR.parent
sys.path.append(str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

try:
    from . import db as usci_db
except ImportError:
    import db as usci_db

try:
    from .entity_resolver import EntityResolver
    from . import prompts
except ImportError:
    from entity_resolver import EntityResolver
    import prompts
from llm_client import run_text_prompt

try:
    from utils import get_db_conn, setup_logger, send_discord
except ImportError:
    def get_db_conn(p): return sqlite3.connect(p)
    def setup_logger(n, f): return logging.getLogger(n)
    async def send_discord(w, c): pass

logger = setup_logger("usci_extractor", "usci_extractor.log")
DB_PATH = BASE_DIR / "tweets.db"
KEYWORDS_PATH = CPO_CHAIN_DIR / "keywords.yaml"

class RelationItem(pydantic.BaseModel):
    from_entity: str
    to_entity: str
    role: str
    role_category: str
    industry_context: str
    evidence_type: str
    confidence: float
    confidence_reason: str

_ISOLATION_RE = re.compile(r'</?(?:TWEET_DATA|NEWS_DATA)>', re.IGNORECASE)


def load_sqlite_vec():
    try:
        return importlib.import_module("sqlite_vec")
    except ModuleNotFoundError:
        return None


def load_vector_dependencies():
    try:
        vec_db_mod = importlib.import_module("cpo_chain.vec_db")
        embedder_mod = importlib.import_module("cpo_chain.embedder")
    except ModuleNotFoundError:
        vec_db_mod = importlib.import_module("vec_db")
        embedder_mod = importlib.import_module("embedder")
    return vec_db_mod, embedder_mod.UniversalEmbedder


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
async def call_gemini(tweets_batch: list[dict]) -> list[dict]:
    """Call Gemini via CLI and extract JSON relations."""
    safe_batch = [{"id": t["id"], "text": _ISOLATION_RE.sub('', t["text"])} for t in tweets_batch]
    content = json.dumps(safe_batch, ensure_ascii=False)
    prompt = f"{prompts.SYSTEM_INSTRUCTION}\n\n{prompts.build_universal_extraction_prompt(content)}"

    try:
        output = run_text_prompt(
            prompt,
            timeout=120,
            backend="auto",
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite"),
        )
        if not output:
            raise ValueError("No output returned from LLM backend")
        # Extract JSON block using regex
        match = re.search(r'(\{.*\})', output, re.DOTALL)
        if not match:
            logger.error(f"Failed to find JSON in output: {output}")
            raise ValueError("No JSON block found in Gemini CLI output")

        data = json.loads(match.group(1))
        return data.get("relations", [])

    except Exception as e:
        logger.error(f"call_gemini Error: {e}")
        raise

async def process_batch(batch_tweets, resolver, conn, dry_run=False):
    """Process batch of tweets for universal supply chain extraction.

    Returns a list of entity names that need review.
    """
    collected_review_entities = []
    try:
        relations = await call_gemini(batch_tweets)
    except Exception as e:
        logger.error(f"Batch failed: {e}")
        if len(batch_tweets) > 1:
            for t in batch_tweets:
                sub_entities = await process_batch([t], resolver, conn, dry_run)
                collected_review_entities.extend(sub_entities)
        return collected_review_entities

    if not relations:
        return collected_review_entities

    to_save = []
    unique_rels = set()
    tweet_map = {t["id"]: t["text"] for t in batch_tweets}

    for rel in relations:
        if rel.get("confidence", 0) < 0.6:
            continue

        role_cat = rel.get("role_category")
        if role_cat not in {'upstream', 'midstream', 'downstream', 'equipment', 'material'}:
            continue

        evidence_type = rel.get("evidence_type") if rel.get("evidence_type") in {"support", "refute"} else "support"
        rel = {**rel, "evidence_type": evidence_type}

        try:
            validated = RelationItem(**rel)
        except Exception:
            continue

        f_name_raw = validated.from_entity
        t_name_raw = validated.to_entity
        role = validated.role
        context = validated.industry_context or "CPO"

        rel_key = (f_name_raw, t_name_raw, role, context)
        if rel_key in unique_rels:
            continue
        unique_rels.add(rel_key)

        try:
            f_id, f_name, f_status = resolver.resolve(conn, f_name_raw)
            t_id, t_name, t_status = resolver.resolve(conn, t_name_raw)

            if f_status == 'needs_review': collected_review_entities.append(f_name)
            if t_status == 'needs_review': collected_review_entities.append(t_name)

            if f_id == t_id: continue

            to_save.append({
                "from_id": f_id,
                "to_id": t_id,
                "role": role,
                "role_category": role_cat,
                "industry_context": context,
                "evidence_type": validated.evidence_type,
                "confidence_reason": validated.confidence_reason,
                "tweet_ids": list(tweet_map.keys()),
                "snippets": [tweet_map[tid] for tid in tweet_map]
            })
            logger.info(f"Extracted [{context}]: {f_name} -> {t_name} ({role})")
        except Exception as e:
            logger.error(f"Resolution Error: {e}")

    if not dry_run and to_save:
        try:
            with conn:
                for item in to_save:
                    score_delta = 1 if item["evidence_type"] == "support" else -1
                    conn.execute("""
                        INSERT INTO industry_relations
                        (from_company_id, to_company_id, role, role_category, industry_context, evidence_score, confidence_reason, base_score, confidence)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 0.5, 0.5)
                        ON CONFLICT(from_company_id, to_company_id, role, industry_context) DO UPDATE SET
                            evidence_score = evidence_score + EXCLUDED.evidence_score,
                            last_confirmed = datetime('now')
                    """, (item["from_id"], item["to_id"], (item["role"] or '')[:100], (item["role_category"] or '')[:50], (item["industry_context"] or '')[:100], score_delta, (item["confidence_reason"] or '')[:500]))

                    cursor = conn.execute("""
                        SELECT id FROM industry_relations
                        WHERE from_company_id = ? AND to_company_id = ? AND role = ? AND industry_context = ?
                    """, (item["from_id"], item["to_id"], item["role"], item["industry_context"]))
                    row = cursor.fetchone()
                    if row:
                        rel_id = row[0]
                        for tid, snip in zip(item["tweet_ids"], item["snippets"]):
                            conn.execute("""
                                INSERT OR IGNORE INTO industry_relation_evidence (relation_id, tweet_id, evidence_type, snippet)
                                VALUES (?, ?, ?, ?)
                            """, (rel_id, tid, item["evidence_type"], snip))
        except Exception as e:
            logger.error(f"DB Error: {e}")

    return collected_review_entities

async def main():
    parser = argparse.ArgumentParser(description="Extract Universal Supply Chain relations")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--vector", action="store_true", help="Use vector search for recall")
    parser.add_argument("--all-tweets", action="store_true", help="Process all unprocessed tweets regardless of keyword match")
    parser.add_argument("--query", type=str, default="Supply chain transactions, assembly, packaging, and raw material relationships for AI, CPO, HBM, Liquid Cooling.")
    args = parser.parse_args()

    conn = get_db_conn(DB_PATH)
    usci_db.init_usci_tables(conn)
    resolver = EntityResolver(DB_PATH, KEYWORDS_PATH)

    tweets = []

    # Stage 1: Recall
    if args.vector:
        logger.info("Using vector search for recall...")
        vec_db, UniversalEmbedder = load_vector_dependencies()
        embedder = UniversalEmbedder()
        query_vec = embedder.embed_query(args.query)

        sqlite_vec = load_sqlite_vec()
        if sqlite_vec is None:
            raise RuntimeError("sqlite_vec is required for --vector mode but is not installed")

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)

        # Query vector index
        query = """
        SELECT t.id, t.text
        FROM tweet_embeddings v
        JOIN tweets t ON CAST(v.tweet_id AS TEXT) = t.id
        WHERE v.tweet_id NOT IN (SELECT tweet_id FROM industry_extract_log)
        ORDER BY vec_distance_cosine(v.embedding, ?)
        LIMIT ?
        """
        tweets = conn.execute(query, (vec_db.serialize_float_list(query_vec), args.limit)).fetchall()
    elif args.all_tweets:
        # All-tweets mode: process every unprocessed tweet, no keyword filter
        logger.info("Using all-tweets mode (no keyword filter)...")
        query = """
        SELECT t.id, t.text
        FROM tweets t
        WHERE t.id NOT IN (SELECT tweet_id FROM industry_extract_log)
        ORDER BY t.created_at DESC
        LIMIT ?
        """
        tweets = conn.execute(query, (args.limit,)).fetchall()
    else:
        # Keyword Recall (Fallback to keywords.yaml if no vector search requested)
        logger.info("Using keyword search for recall...")
        with open(KEYWORDS_PATH, "r", encoding="utf-8") as f:
            keywords = yaml.safe_load(f).get("keywords", [])

        fts_query = " OR ".join([f'"{k}"' for k in keywords])
        query = """
        SELECT t.id, t.text
        FROM tweets t
        JOIN tweets_fts f ON t.rowid = f.rowid
        WHERE t.id NOT IN (SELECT tweet_id FROM industry_extract_log)
        AND f.tweets_fts MATCH ?
        LIMIT ?
        """
        tweets = conn.execute(query, (fts_query, args.limit)).fetchall()

    logger.info(f"Found {len(tweets)} tweets to process.")
    new_review_entities = []

    batch_size = 5
    for i in range(0, len(tweets), batch_size):
        batch = [{"id": r[0], "text": r[1]} for r in tweets[i:i+batch_size]]
        batch_entities = await process_batch(batch, resolver, conn, dry_run=args.dry_run)
        new_review_entities.extend(batch_entities)

        if not args.dry_run:
            with conn:
                for t in batch:
                    conn.execute("INSERT OR IGNORE INTO industry_extract_log (tweet_id) VALUES (?)", (t["id"],))

    if not args.dry_run and new_review_entities:
        unique_new = list(set(new_review_entities))
        alert_msg = f"🔎 **USCI 新實體待審核提醒**\n辨識出 {len(unique_new)} 個未知實體：\n" + "\n".join([f"- {e}" for e in unique_new[:10]])
        webhook = os.environ.get("DISCORD_WEBHOOK_SERENITY")
        if webhook: await send_discord(webhook, alert_msg)

    conn.close()
    logger.info("USCI Extraction completed.")

if __name__ == "__main__":
    asyncio.run(main())
