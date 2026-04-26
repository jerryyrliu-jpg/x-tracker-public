"""
1. Update tickers on existing entities that are missing them
2. Delete duplicate entities created by import_cpo_chain.py
3. Insert all CPO relations using correct entity IDs
"""
import sqlite3, sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "tweets.db"

# Existing entities (by name fragment) → correct ticker
TICKER_UPDATES = [
    ("Lumentum",               "LITE"),
    ("Coherent",               "COHR"),
    ("Sivers Semiconductors",  "SIVE"),
    ("Aehr Test Systems",      "AEHR"),
    ("GlobalFoundries",        "GFS"),
    ("IQE",                    "IQE"),
    ("WIN Semiconductors",     "WIN"),
    ("ASE Technology",         "ASX"),
    ("Teradyne",               "TER"),
    ("Keysight Technologies",  "KEYS"),
    ("Plan Optik",             "POPT"),
    ("Fabrinet",               "FN"),
    ("TTM Technologies",       "TTMI"),
    ("Shin-Etsu Chemical",     "SHECY"),
]

# Duplicates to remove: (name_to_delete, keep_ticker)
# These were created by import_cpo_chain.py and duplicate older entries
DUPLICATES_TO_REMOVE = [
    "AXT Inc",              # keep "AXT Inc." id=50 → will give AXTI to id=50
    "Win Semiconductors",   # keep "WIN Semiconductors" id=10
    "Shunsin Technology",   # keep "ShunSin" id=67
    "FOCI Fiber Optic Communications",  # keep "FOCI" id=64
]

# For AXT: the old entry "AXT Inc." id=50 should get ticker AXTI
EXTRA_TICKER_UPDATES = [
    ("AXT Inc.",    "AXTI"),
    ("ShunSin",     "6451"),
    ("FOCI",        "FOCI"),
    ("Soitec",      "SOITF"),
]

RELATIONS = [
    # (supplier_ticker, customer_ticker, role, confidence, role_category)
    ("AXTI",  "AAOI",  "InP substrate supplier for laser production", 0.85, "material"),
    ("AXTI",  "LITE",  "InP substrate supplier for EML lasers", 0.85, "material"),
    ("AXTI",  "COHR",  "InP substrate supplier for EML lasers", 0.80, "material"),
    ("AXTI",  "SIVE",  "InP substrate supplier for DFB lasers", 0.80, "material"),
    ("AXTI",  "MRVL",  "InP substrate — critical CPO supply chain chokepoint", 0.75, "material"),
    ("SHECY", "AXTI",  "pBN crucible and B2O3 feedstock for InP reactors", 0.70, "material"),
    ("SOI",   "TSEM",  "Photonic-SOI wafer supplier for silicon photonics", 0.85, "material"),
    ("SOI",   "GFS",   "Photonic-SOI wafer supplier for silicon photonics", 0.80, "material"),
    ("WIN",   "AAOI",  "InP/GaAs compound semiconductor foundry", 0.80, "upstream"),
    ("WIN",   "LITE",  "InP/GaAs compound semiconductor foundry", 0.75, "upstream"),
    ("WIN",   "SIVE",  "InP/GaAs foundry for DFB laser dies", 0.75, "upstream"),
    ("WIN",   "AVGO",  "GaAs foundry partner (AVGO holds ~5% stake)", 0.85, "upstream"),
    ("TSEM",  "NVDA",  "Silicon photonics foundry, 1.6T CPO collaboration", 0.90, "upstream"),
    ("GFS",   "MRVL",  "Silicon photonics foundry partner", 0.75, "upstream"),
    ("CDNS",  "TSEM",  "EDA tools for photonic IC design", 0.70, "equipment"),
    ("SNPS",  "GFS",   "EDA and multiphysics simulation for SiPh", 0.70, "equipment"),
    ("AAOI",  "NVDA",  "CW/DFB laser supplier for CPO modules", 0.85, "upstream"),
    ("AAOI",  "MSFT",  "Laser supplier — hyperscaler capacity booking", 0.80, "upstream"),
    ("AAOI",  "GOOGL", "Laser supplier — hyperscaler capacity booking", 0.80, "upstream"),
    ("LITE",  "NVDA",  "EML laser supplier for 1.6T optical modules", 0.85, "upstream"),
    ("LITE",  "MRVL",  "Laser and optical component supplier", 0.80, "upstream"),
    ("COHR",  "NVDA",  "EML laser supplier for CPO", 0.80, "upstream"),
    ("SIVE",  "MRVL",  "DFB laser supplier for 1.6T LRO modules (JBL confirmed)", 0.85, "upstream"),
    ("SIVE",  "FN",    "DFB laser dies for Fabrinet module assembly", 0.75, "upstream"),
    ("MRVL",  "NVDA",  "Optical DSP and custom AI chip (CPO partner)", 0.85, "upstream"),
    ("MRVL",  "MSFT",  "Custom AI chip Maia supplier", 0.85, "upstream"),
    ("MRVL",  "GOOGL", "Custom AI chip TPU co-development", 0.85, "upstream"),
    ("AVGO",  "NVDA",  "Optical DSP and network switch ASIC", 0.80, "upstream"),
    ("AEHR",  "TSEM",  "Wafer-level burn-in testing for silicon photonics", 0.85, "equipment"),
    ("ONTO",  "GLW",   "Glass core substrate metrology partner (LIDE)", 0.80, "equipment"),
    ("FN",    "NVDA",  "Optical module EMS — ~35% revenue from NVIDIA", 0.90, "downstream"),
    ("6451",  "NVDA",  "CPO packaging and module assembly (Foxconn subsidiary)", 0.80, "downstream"),
    ("6451",  "MRVL",  "CPO module packaging partner", 0.75, "downstream"),
    ("ASX",   "NVDA",  "Advanced packaging for CPO mass production", 0.80, "downstream"),
    ("GLW",   "META",  "Optical fiber supplier — $6B contract", 0.95, "upstream"),
    ("GLW",   "NVDA",  "Fiber and glass core substrate supplier", 0.75, "upstream"),
    ("LPKK",  "GLW",   "LIDE glass substrate technology partner", 0.75, "equipment"),
    ("LPKK",  "ONTO",  "Glass core substrate co-development metrology", 0.80, "equipment"),
    ("IQE",   "LITE",  "InP epiwafer supplier for lasers", 0.85, "material"),
    ("IQE",   "COHR",  "InP epiwafer supplier for lasers", 0.80, "material"),
    ("SMTOY", "AAOI",  "InP substrate supplier", 0.75, "material"),
    ("JXNMF", "LITE",  "InP substrate supplier", 0.75, "material"),
]

def run():
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA foreign_keys=OFF")

    # 1. Update tickers on existing entries
    for name_frag, ticker in TICKER_UPDATES + EXTRA_TICKER_UPDATES:
        conn.execute(
            "UPDATE industry_entities SET ticker=? WHERE name=? AND (ticker IS NULL OR ticker='')",
            (ticker, name_frag)
        )
    conn.commit()
    print("Tickers updated on existing entities.")

    # 2. Remove duplicates (merge relations if any, then delete)
    for dup_name in DUPLICATES_TO_REMOVE:
        row = conn.execute("SELECT id FROM industry_entities WHERE name=?", (dup_name,)).fetchone()
        if row:
            dup_id = row[0]
            conn.execute("DELETE FROM industry_entity_aliases WHERE company_id=?", (dup_id,))
            conn.execute("DELETE FROM industry_relation_evidence WHERE relation_id IN (SELECT id FROM industry_relations WHERE from_company_id=? OR to_company_id=?)", (dup_id, dup_id))
            conn.execute("DELETE FROM industry_relations WHERE from_company_id=? OR to_company_id=?", (dup_id, dup_id))
            conn.execute("DELETE FROM industry_entities WHERE id=?", (dup_id,))
            print(f"  Removed duplicate: {dup_name} (id={dup_id})")
    conn.commit()

    # 3. Build ticker map
    ticker_map = {}
    for r in conn.execute("SELECT ticker, id FROM industry_entities WHERE ticker IS NOT NULL AND ticker != ''").fetchall():
        ticker_map[r[0]] = r[1]

    # 4. Insert relations
    added = skipped = missing = 0
    for sup_t, cust_t, role, conf, role_cat in RELATIONS:
        sup_id = ticker_map.get(sup_t)
        cust_id = ticker_map.get(cust_t)
        if not sup_id or not cust_id:
            print(f"  MISSING: {sup_t}({sup_id}) → {cust_t}({cust_id})")
            missing += 1
            continue
        cur = conn.execute("""
            INSERT OR IGNORE INTO industry_relations
            (from_company_id, to_company_id, role, role_category, industry_context, confidence, status)
            VALUES (?,?,?,?,'CPO',?,'active')
        """, (sup_id, cust_id, role, role_cat, conf))
        if cur.rowcount > 0:
            added += 1
        else:
            skipped += 1

    conn.commit()
    conn.execute("PRAGMA foreign_keys=ON")
    conn.close()
    print(f"\nRelations: +{added} added, {skipped} already existed, {missing} missing entity")
    print("Done. Run export_universal + update_network_html.py to refresh.")

if __name__ == "__main__":
    run()
