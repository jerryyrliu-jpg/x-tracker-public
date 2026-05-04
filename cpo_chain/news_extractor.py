import sqlite3
import json
import logging
import re
import subprocess
from .entity_resolver import EntityResolver
from .company_ticker_mapper import CompanyTickerMapper

logger = logging.getLogger("news_extractor")

INDUSTRY_CONTEXTS = ["CPO", "HBM", "AI_Server", "Liquid_Cooling", "Advanced_Packaging", "Other"]

EXTRACTION_PROMPT = """
你是供應鏈分析師。從以下新聞標題與摘要中，識別明確的供應鏈關係。

規則：
- 只萃取有明確 supplier/customer/manufacturer/partner 關係的內容
- 不確定的關係請忽略
- industry_context 必須從以下選擇：CPO, HBM, AI_Server, Liquid_Cooling, Advanced_Packaging, Other

輸出嚴格 JSON（無其他文字）：
{{"relations": [{{"supplier": "公司A", "customer": "公司B", "role": "角色描述", "industry_context": "CPO"}}]}}
若無明確關係：{{"relations": []}}

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

    def extract_from_article(self, article: dict) -> list[dict]:
        """Call gemini CLI to extract relations. Raises on error."""
        text = f"{article['title'][:300]}. {article.get('summary', '')[:280]}"
        prompt = EXTRACTION_PROMPT.format(text=text.replace("{", "{{").replace("}", "}}"))
        result = subprocess.run(
            ["gemini", "-p", prompt],
            capture_output=True, text=True, encoding="utf-8",
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"gemini CLI error: {result.stderr[:200]}")
        match = re.search(r'(\{.*\})', result.stdout, re.DOTALL)
        if not match:
            return []
        data = json.loads(match.group(1))
        relations = data.get("relations", [])
        # Validate industry_context against enum
        valid = set(INDUSTRY_CONTEXTS)
        for rel in relations:
            if rel.get("industry_context") not in valid:
                rel["industry_context"] = "Other"
        return relations

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
                inner_errors = 0

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
                                WHERE from_company_id=? AND to_company_id=? AND role=? AND industry_context=?
                            """, (from_id, to_id, rel.get("role", "supplier"), rel.get("industry_context", "Other"))).fetchone()
                            rel_id = row[0] if row else None
                            skipped += 1

                        if rel_id is not None:
                            conn.execute("""
                                INSERT OR IGNORE INTO industry_relation_evidence
                                (relation_id, tweet_id, snippet, source)
                                VALUES (?, ?, ?, 'news')
                            """, (rel_id, a["url"], a["title"][:200]))
                            has_relations = True

                    except Exception as e:
                        logger.error(f"Relation insert error: {e}")
                        errors += 1
                        inner_errors += 1

                # Determine processed state
                if inner_errors > 0 and not has_relations and relations:
                    new_state = 3  # had relations from LLM but all failed to insert
                else:
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
