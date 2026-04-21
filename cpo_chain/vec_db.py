import sqlite3
import sqlite_vec
import struct

def init_vector_tables(conn):
    """
    Initialize vector tables for tweet embeddings using sqlite-vec.
    The primary key for vec0 MUST be an integer.
    """
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    
    # In sqlite-vec vec0, the first column is the integer primary key
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS tweet_embeddings USING vec0(
            tweet_id INTEGER PRIMARY KEY,
            embedding FLOAT[768]
        );
    """)
    conn.commit()

def serialize_float_list(floats):
    return struct.pack(f'{len(floats)}f', *floats)

if __name__ == "__main__":
    db_path = "tweets.db"
    conn = sqlite3.connect(db_path)
    try:
        init_vector_tables(conn)
        print("Successfully initialized vector tables.")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        conn.close()
