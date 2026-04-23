import json
from pathlib import Path

def test_repro():
    cache_path = Path("/Users/yj/Desktop/PyProjects/X-tracker/cpo_chain/output/usci_tiers_cache.json")
    if not cache_path.exists():
        print("Cache file not found")
        return

    with open(cache_path, 'r', encoding='utf-8') as f:
        full_data = json.load(f)

    industry = "CPO"
    company_search = "NVDA"
    
    industry_data = full_data.get("industries", {}).get(industry.upper())
    if not industry_data:
        print(f"Industry {industry} not found")
        return

    tiers_list = industry_data.get("tiers", [])
    
    found = []
    for item in tiers_list:
        t_name = item.get("name", "Unknown")
        # Current logic in discord_bot.py:
        if company_search and company_search.upper() not in t_name.upper():
            continue
        found.append(item)
    
    print(f"Search for '{company_search}' in '{industry}':")
    if not found:
        print("FAILED to find company")
    else:
        for f in found:
            print(f"Found: {f['name']} (ID: {f['id']})")

    # Now test with NVIDIA as name
    company_search = "NVIDIA"
    found = []
    for item in tiers_list:
        t_name = item.get("name", "Unknown")
        if company_search and company_search.upper() not in t_name.upper():
            continue
        found.append(item)
    print(f"\nSearch for '{company_search}' in '{industry}':")
    if not found:
        print("FAILED to find company")
    else:
        for f in found:
            print(f"Found: {f['name']} (ID: {f['id']})")

if __name__ == "__main__":
    test_repro()
