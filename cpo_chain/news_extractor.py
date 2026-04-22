import sqlite3
import json
import logging
import google.generativeai as genai
from google.generativeai.types import GenerationConfig
from .entity_resolver import EntityResolver
from .company_ticker_mapper import CompanyTickerMapper

logger = logging.getLogger("news_extractor")

INDUSTRY_CONTEXTS = ["CPO", "HBM", "AI_Server", "Liquid_Cooling", "Advanced_Packaging", "Other"]

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "relations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "supplier": {"type": "string"},
                    "customer": {"type": "string"},
                    "role": {"type": "string"},
                    "industry_context": {"type": "string", "enum": INDUSTRY_CONTEXTS}
                },
                "required": ["supplier", "customer", "role", "industry_context"]
            }
        }
    },
    "required": ["relations"]
}

EXTRACTION_PROMPT = """
你是供應鏈分析師。從以下新聞標題與摘要中，識別明確的供應鏈關係。

規則：
- 只萃取有明確 supplier/customer/manufacturer/partner 關係的內容
- 不確定的關係請忽略
- industry_context 必須從以下選擇：CPO, HBM, AI_Server, Liquid_Cooling, Advanced_Packaging, Other

新聞：{text}
"""

SOURCE_BASE_SCORE = {
    "google_news": 0.55,
    "sec_8k": 0.80,
}


class NewsExtractor:
    def __init__(self, db_path, keywords_path):
        self.db_path = db_path
        self.resolver = EntityResolver(db_path, keywords_path)
        self.model = genai.GenerativeModel(
            "gemini-2.0-flash",
            generation_config=GenerationConfig(
                response_mime_type="application/json",
                response_schema=EXTRACTION_SCHEMA,
                max_output_tokens=1024,
            )
        )

    def extract_from_article(self, article: dict) -> list[dict]:
        """Send article to Gemini, return list of relation dicts. Raises on error."""
        text = f"{article['title']}. {article.get('summary', '')}"[:600]
        resp = self.model.generate_content(EXTRACTION_PROMPT.format(text=text))
        data = json.loads(resp.text.strip())
        return data.get("relations", [])

    def run(self, conn: sqlite3.Connection, limit: int = 50) -> dict:
        """
        Fetch processed=0 articles → extract → write to industry_relations → mark processed.
        Returns {"added": N, "skipped": M, "errors": K}
        """
        articles = conn.execute("""
            SELECT id, url, source, title, summary
            FROM news_articles
            WHERE processed = 0
            ORDER BY fetched_at ASC
            LIMIT ?
        """, (limit,)).fetchall()

        added = skipped = errors = 0
        for article in articles:
            try:
                a = dict(article)
                relations = self.extract_from_article(a)
                base_score = SOURCE_BASE_SCORE.get(a["source"], 0.55)
                has_relations = False

                for rel in relations:
                    try:
                        supplier = rel.get("supplier", "").strip()
                        customer = rel.get("customer", "").strip()
                        if not supplier or not customer:
                            continue

                        from_id, _, _ = self.resolver.resolve(conn, supplier)
                        to_id, _, _ = self.resolver.resolve(conn, customer)

                        conn.execute("""
                            INSERT OR IGNORE INTO industry_relations
                            (from_company_id, to_company_id, role, role_category,
                             base_score, confidence, industry_context)
                            VALUES (?, ?, ?, 'upstream', ?, ?, ?)
                        """, (from_id, to_id,
                              rel.get("role", "supplier"),
                              base_score, base_score,
                              rel.get("industry_context", "Other")))

                        if conn.execute("SELECT changes()").fetchone()[0]:
                            rel_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                            added += 1
                        else:
                            # Relation exists (from Twitter) — fetch its id for evidence
                            row = conn.execute("""
                                SELECT id FROM industry_relations
                                WHERE from_company_id=? AND to_company_id=?
                            """, (from_id, to_id)).fetchone()
                            rel_id = row[0] if row else None
                            skipped += 1

                        if rel_id is not None:
                            # INSERT OR REPLACE — ensures news evidence not silently dropped
                            conn.execute("""
                                INSERT OR REPLACE INTO industry_relation_evidence
                                (relation_id, tweet_id, snippet, source)
                                VALUES (?, ?, ?, 'news')
                            """, (rel_id, a["url"], a["title"][:200]))
                        has_relations = True

                    except Exception as e:
                        logger.error(f"Relation insert error: {e}")
                        errors += 1

                new_state = 1 if has_relations else 2
                conn.execute("UPDATE news_articles SET processed=? WHERE id=?",
                             (new_state, a["id"]))
                conn.commit()

            except Exception as e:
                logger.error(f"Article {article['id']} error: {e}")
                conn.execute("UPDATE news_articles SET processed=3 WHERE id=?", (article["id"],))
                conn.commit()
                errors += 1

        return {"added": added, "skipped": skipped, "errors": errors}
