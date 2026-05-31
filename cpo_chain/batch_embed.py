import asyncio
import sqlite3
import sys
import logging
from pathlib import Path

# Setup paths
CPO_CHAIN_DIR = Path(__file__).resolve().parent
BASE_DIR = CPO_CHAIN_DIR.parent
sys.path.append(str(BASE_DIR))

try:
    from . import vec_db
    from .embedder import UniversalEmbedder
except ImportError:
    import vec_db
    from embedder import UniversalEmbedder

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("usci_batch_embed")
DB_PATH = BASE_DIR / "tweets.db"

async def batch_embed(limit=1000):
    conn = sqlite3.connect(DB_PATH)
    vec_db.init_vector_tables(conn)
    
    # Find tweets that haven't been embedded yet
    # CAST id to INTEGER to match tweet_id type
    query = """
    SELECT id, text FROM tweets 
    WHERE CAST(id AS INTEGER) NOT IN (SELECT tweet_id FROM tweet_embeddings)
    LIMIT ?
    """
    tweets = conn.execute(query, (limit,)).fetchall()
    
    if not tweets:
        logger.info("No new tweets to embed.")
        conn.close()
        return

    logger.info(f"Embedding {len(tweets)} tweets...")
    embedder = UniversalEmbedder()
    
    batch_size = 50
    for i in range(0, len(tweets), batch_size):
        batch = tweets[i:i+batch_size]
        ids = [t[0] for t in batch]
        texts = [t[1] for t in batch]
        
        embeddings = embedder.embed_texts(texts)
        
        with conn:
            for tid, emb in zip(ids, embeddings):
                try:
                    int_id = int(tid)
                    # Use a separate DELETE then INSERT to be safe with virtual tables
                    conn.execute("DELETE FROM tweet_embeddings WHERE tweet_id = ?", (int_id,))
                    conn.execute(
                        "INSERT INTO tweet_embeddings (tweet_id, embedding) VALUES (?, ?)",
                        (int_id, vec_db.serialize_float_list(emb))
                    )
                except ValueError:
                    logger.warning(f"Skipping non-integer tweet_id: {tid}")
                except Exception as e:
                    logger.error(f"Error inserting {tid}: {e}")
        logger.info(f"Processed {i + len(batch)}/{len(tweets)}")

    conn.close()
    logger.info("Batch embedding complete.")

if __name__ == "__main__":
    asyncio.run(batch_embed())
