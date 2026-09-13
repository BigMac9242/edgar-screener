"""Offline test: feed screener.build() synthetic frames and check the maths.

The sandbox can't reach data.sec.gov, so we stub fetch() and verify that the
join, the nearest-balance-sheet matching and the DuPont identity all behave.
"""
import json
import screener


def frame(tag, entries, ccp):
    return {"taxonomy": "us-gaap", "tag": tag, "ccp": ccp, "uom": "USD",
            "pts": len(entries), "data": entries}


def e(cik, name, val, end, start=None):
    d = {"accn": "x", "cik": cik, "entityName": name, "loc": "US-CA",
         "end": end, "val": val}
    if start:
        d["start"] = start
    return d


# Company 1: Dec year end, clean.   Company 2: Sept year end (like Apple),
# so its balance sheet only exists in the Q3 instant frame.
# Company 3: negative capital employed -> must be dropped.
# Company 4: revenue below the floor -> must be dropped.
FRAMES = {
    "OperatingIncomeLoss/USD/CY2024.json": frame("OperatingIncomeLoss", [
        e(1, "Dec Co", 200, "2024-12-31", "2024-01-01"),
        e(2, "Sept Co", 500, "2024-09-28", "2023-10-01"),
        e(3, "Negative Co", 50, "2024-12-31", "2024-01-01"),
        e(4, "Tiny Co", 10, "2024-12-31", "2024-01-01"),
    ], "CY2024"),
    "RevenueFromContractWithCustomerExcludingAssessedTax/USD/CY2024.json": frame("Rev", [
        e(1, "Dec Co", 1000, "2024-12-31"),
        e(2, "Sept Co", 2000, "2024-09-28"),
        e(3, "Negative Co", 1500, "2024-12-31"),
        e(4, "Tiny Co", 50, "2024-12-31"),
    ], "CY2024"),
    "NetIncomeLoss/USD/CY2024.json": frame("NetIncomeLoss", [
        e(1, "Dec Co", 150, "2024-12-31"),
        e(2, "Sept Co", 400, "2024-09-28"),
    ], "CY2024"),
    "Assets/USD/CY2024Q4I.json": frame("Assets", [
        e(1, "Dec Co", 900, "2024-12-31"),
        e(3, "Negative Co", 300, "2024-12-31"),
        e(4, "Tiny Co", 100, "2024-12-31"),
    ], "CY2024Q4I"),
    "Assets/USD/CY2024Q3I.json": frame("Assets", [
        e(2, "Sept Co", 1600, "2024-09-28"),
    ], "CY2024Q3I"),
    "LiabilitiesCurrent/USD/CY2024Q4I.json": frame("LiabilitiesCurrent", [
        e(1, "Dec Co", 400, "2024-12-31"),
        e(3, "Negative Co", 500, "2024-12-31"),   # > assets
        e(4, "Tiny Co", 20, "2024-12-31"),
    ], "CY2024Q4I"),
    "LiabilitiesCurrent/USD/CY2024Q3I.json": frame("LiabilitiesCurrent", [
        e(2, "Sept Co", 600, "2024-09-28"),
    ], "CY2024Q3I"),
    "StockholdersEquity/USD/CY2024Q4I.json": frame("StockholdersEquity", [
        e(1, "Dec Co", 300, "2024-12-31"),
    ], "CY2024Q4I"),
    "StockholdersEquity/USD/CY2024Q3I.json": frame("StockholdersEquity", [
        e(2, "Sept Co", 800, "2024-09-28"),
    ], "CY2024Q3I"),
}


def fake_fetch(url, retries=3):
    for suffix, body in FRAMES.items():
        if url.endswith(suffix):
            return body
    return None


screener.fetch = fake_fetch
rows = screener.build(2024, min_revenue=100, verbose=False)

print("\n--- results ---")
for r in rows:
    print(f"{r['name']:<14} margin={r['operating_margin']:.3f} "
          f"turnover={r['capital_turnover']:.3f} roce={r['roce']:.3f} "
          f"cap_employed={r['capital_employed']}")

names = [r["name"] for r in rows]
assert "Negative Co" not in names, "negative capital employed should be dropped"
assert "Tiny Co" not in names, "sub-threshold revenue should be dropped"
assert "Sept Co" in names, "non-December year end should still match a balance sheet"
assert len(rows) == 2, f"expected 2 rows, got {len(rows)}"

# Sept Co: capital employed = 1600 - 600 = 1000; margin = 500/2000 = 0.25;
# turnover = 2000/1000 = 2.0; ROCE = 500/1000 = 0.50
s = next(r for r in rows if r["name"] == "Sept Co")
assert s["capital_employed"] == 1000
assert abs(s["operating_margin"] - 0.25) < 1e-9
assert abs(s["capital_turnover"] - 2.0) < 1e-9
assert abs(s["roce"] - 0.50) < 1e-9

# Dec Co: capital employed = 900 - 400 = 500; margin = 0.2; turnover = 2.0; ROCE = 0.4
d = next(r for r in rows if r["name"] == "Dec Co")
assert abs(d["roce"] - 0.40) < 1e-9

# the DuPont identity must hold for every row
for r in rows:
    assert abs(r["operating_margin"] * r["capital_turnover"] - r["roce"]) < 1e-12

# sorted by ROCE descending
assert rows[0]["name"] == "Sept Co"

print("\nAll assertions passed.")
