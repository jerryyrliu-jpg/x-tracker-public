"""
Import CPO supply chain data from jimmyhuli Substack article into USCI DB.
Source: https://jimmyhuli.substack.com/p/serenity-x-21
"""
import sqlite3
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "tweets.db"

ENTITIES = [
    # (name, ticker, country, industry_tags)
    # Layer 1: InP Substrate
    ("AXT Inc", "AXTI", "US", "CPO,InP,substrate"),
    ("Sumitomo Electric", "SMTOY", "JP", "CPO,InP,substrate"),
    ("JX Nippon Mining", "JXNMF", "JP", "CPO,InP,substrate"),
    # Layer 2: InP Feedstock
    ("Shin-Etsu Chemical", "SHECY", "JP", "CPO,substrate,materials"),
    ("Morgan Advanced Materials", "MGAM", "UK", "CPO,materials"),
    # Layer 3: Photonic-SOI
    ("Soitec", "SOITF", "FR", "CPO,SOI,substrate"),
    # Layer 4: Compound Semi Foundry
    ("Win Semiconductors", "WIN", "TW", "CPO,foundry,InP,GaAs"),
    # Layer 5: Silicon Photonics Foundry
    ("Tower Semiconductor", "TSEM", "IL", "CPO,SiPh,foundry"),
    ("GlobalFoundries", "GFS", "US", "CPO,SiPh,foundry"),
    # Layer 6: EDA
    ("Cadence Design Systems", "CDNS", "US", "CPO,EDA"),
    ("Synopsys", "SNPS", "US", "CPO,EDA"),
    # Layer 7: Lasers
    ("Applied Optoelectronics", "AAOI", "US", "CPO,laser,photonics"),
    ("Lumentum", "LITE", "US", "CPO,laser,EML"),
    ("Coherent", "COHR", "US", "CPO,laser,EML"),
    ("Sivers Semiconductors", "SIVE", "SE", "CPO,laser,DFB"),
    # Layer 8: Optical DSP
    ("Marvell Technology", "MRVL", "US", "CPO,DSP,AI_chip"),
    ("Broadcom", "AVGO", "US", "CPO,DSP,optical"),
    # Layer 9: Test & Burn-in
    ("Aehr Test Systems", "AEHR", "US", "CPO,test,burn-in"),
    ("Onto Innovation", "ONTO", "US", "CPO,test,metrology"),
    ("Keysight Technologies", "KEYS", "US", "CPO,test,signal"),
    ("Teradyne", "TER", "US", "CPO,test"),
    # Layer 10: Module Assembly
    ("Fabrinet", "FN", "TH", "CPO,module,EMS"),
    ("Shunsin Technology", "6451", "TW", "CPO,module,packaging"),
    ("LuxNet", "LUXN", "TW", "CPO,module,optical"),
    ("FOCI Fiber Optic Communications", "FOCI", "TW", "CPO,module,optical"),
    # Layer 11: OSAT
    ("ASE Technology", "ASX", "TW", "CPO,OSAT,packaging"),
    # Layer 12: Fiber & Glass Substrate
    ("Corning", "GLW", "US", "CPO,fiber,glass"),
    ("LPKF Laser & Electronics", "LPKK", "DE", "CPO,glass,substrate,LIDE"),
    # Hyperscalers / End Customers
    ("NVIDIA", "NVDA", "US", "CPO,AI,hyperscaler"),
    ("Microsoft", "MSFT", "US", "CPO,hyperscaler,cloud"),
    ("Alphabet", "GOOGL", "US", "CPO,hyperscaler,cloud"),
    ("Amazon", "AMZN", "US", "CPO,hyperscaler,cloud"),
    ("Meta", "META", "US", "CPO,hyperscaler,cloud"),
]

RELATIONS = [
    # (supplier_ticker, customer_ticker, role, confidence)
    # Layer 1 → Layer 7 (InP substrate to laser makers)
    ("AXTI", "AAOI", "InP substrate supplier for laser production", 0.85),
    ("AXTI", "LITE", "InP substrate supplier for EML lasers", 0.85),
    ("AXTI", "COHR", "InP substrate supplier for EML lasers", 0.80),
    ("AXTI", "SIVE", "InP substrate supplier for DFB lasers", 0.80),
    # Layer 2 → Layer 1
    ("SHECY", "AXTI", "pBN crucible and B2O3 feedstock for InP reactors", 0.70),
    # Layer 3 → Layer 5 (SOI wafer to SiPh foundries)
    ("SOITF", "TSEM", "Photonic-SOI wafer supplier for silicon photonics", 0.85),
    ("SOITF", "GFS", "Photonic-SOI wafer supplier for silicon photonics", 0.80),
    # Layer 4 → Layer 7 (Compound semi foundry to laser makers)
    ("WIN", "AAOI", "InP/GaAs compound semiconductor foundry", 0.80),
    ("WIN", "LITE", "InP/GaAs compound semiconductor foundry", 0.75),
    ("WIN", "SIVE", "InP/GaAs foundry for DFB laser dies", 0.75),
    ("WIN", "AVGO", "GaAs foundry partner (AVGO holds ~5% stake)", 0.85),
    # Layer 5 → Hyperscalers (SiPh foundry to end customers)
    ("TSEM", "NVDA", "Silicon photonics foundry, 1.6T CPO collaboration", 0.90),
    ("GFS", "MRVL", "Silicon photonics foundry partner", 0.75),
    # Layer 6 (EDA to foundries)
    ("CDNS", "TSEM", "EDA tools for photonic IC design", 0.70),
    ("SNPS", "GFS", "EDA and multiphysics simulation for SiPh", 0.70),
    # Layer 7 → Hyperscalers (Lasers to customers)
    ("AAOI", "NVDA", "CW/DFB laser supplier for CPO modules", 0.85),
    ("AAOI", "MSFT", "Laser supplier — hyperscaler capacity booking", 0.80),
    ("AAOI", "GOOGL", "Laser supplier — hyperscaler capacity booking", 0.80),
    ("LITE", "NVDA", "EML laser supplier for 1.6T optical modules", 0.85),
    ("LITE", "MRVL", "Laser and optical component supplier", 0.80),
    ("COHR", "NVDA", "EML laser supplier for CPO", 0.80),
    ("SIVE", "MRVL", "DFB laser supplier for 1.6T LRO modules", 0.85),
    ("SIVE", "FN", "DFB laser dies for Fabrinet module assembly", 0.75),
    # Layer 8 → Hyperscalers (DSP/Network chips)
    ("MRVL", "NVDA", "Optical DSP and custom AI chip (CPO partner)", 0.85),
    ("MRVL", "MSFT", "Custom AI chip (Maia) supplier", 0.85),
    ("MRVL", "GOOGL", "Custom AI chip (TPU co-development)", 0.85),
    ("AVGO", "NVDA", "Optical DSP and network switch ASIC", 0.80),
    ("AVGO", "MSFT", "Network/optical components", 0.75),
    # Layer 9 → Layer 5 (Test to SiPh foundries)
    ("AEHR", "TSEM", "Wafer-level burn-in testing for silicon photonics", 0.85),
    ("ONTO", "GLW", "Glass core substrate metrology partner (LIDE)", 0.80),
    # Layer 10 → Hyperscalers (Module assembly)
    ("FN", "NVDA", "Optical module EMS — ~35% revenue from NVIDIA", 0.90),
    ("6451", "NVDA", "CPO packaging and module assembly (Foxconn subsidiary)", 0.80),
    ("6451", "MRVL", "CPO module packaging partner", 0.75),
    # Layer 11 → Layer 10 (OSAT to module)
    ("ASX", "NVDA", "Advanced packaging for CPO mass production", 0.80),
    # Layer 12 → Hyperscalers (Fiber/glass)
    ("GLW", "META", "Optical fiber supplier — $6B contract", 0.95),
    ("GLW", "NVDA", "Fiber and glass core substrate supplier", 0.75),
    ("LPKK", "GLW", "LIDE glass substrate technology partner", 0.75),
    ("LPKK", "ONTO", "Glass core substrate co-development (metrology)", 0.80),
]

def run():
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA foreign_keys=OFF")

    # Ensure tables exist
    sys.path.insert(0, str(BASE))
    from cpo_chain.db import init_usci_tables
    init_usci_tables(conn)

    added_entities = 0
    for name, ticker, country, tags in ENTITIES:
        cur = conn.execute(
            "INSERT OR IGNORE INTO industry_entities (name, ticker, country, industry_tags) VALUES (?,?,?,?)",
            (name, ticker, country, tags)
        )
        if cur.rowcount > 0:
            added_entities += 1

    conn.commit()
    print(f"Entities: +{added_entities} added")

    # Build ticker → id map
    ticker_map = {r[0]: r[1] for r in conn.execute("SELECT ticker, id FROM industry_entities").fetchall()}

    added_rel = 0
    skipped_rel = 0
    for sup_ticker, cust_ticker, role, conf in RELATIONS:
        sup_id = ticker_map.get(sup_ticker)
        cust_id = ticker_map.get(cust_ticker)
        if not sup_id or not cust_id:
            print(f"  SKIP (not found): {sup_ticker} → {cust_ticker}")
            skipped_rel += 1
            continue
        cur = conn.execute("""
            INSERT OR IGNORE INTO industry_relations
            (from_company_id, to_company_id, role, industry_context, confidence, status)
            VALUES (?,?,?,'CPO',?,'active')
        """, (sup_id, cust_id, role, conf))
        if cur.rowcount > 0:
            added_rel += 1
        else:
            skipped_rel += 1

    conn.commit()
    conn.execute("PRAGMA foreign_keys=ON")
    conn.close()
    print(f"Relations: +{added_rel} added, {skipped_rel} skipped (already exist)")
    print("Done. Run export_universal to refresh cache and HTML.")

if __name__ == "__main__":
    run()
