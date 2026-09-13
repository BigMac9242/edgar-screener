#!/usr/bin/env python3
"""
edgar-screener — rank US-listed companies on the ratios from the VW EE.

Pulls XBRL "frames" from SEC EDGAR (free, no API key, official source),
joins them by CIK, and computes an operating DuPont decomposition:

    ROCE  =  operating margin  x  capital turnover
             (EBIT / revenue)     (revenue / capital employed)

    capital employed = total assets - current liabilities

Stdlib only. No pip install needed.

Usage:
    python3 screener.py --year 2024 --min-revenue 1e9 --out data.json
    python3 screener.py --year 2024 --enrich-sic 100          # add industry codes
    python3 screener.py --year 2023 --out data2023.json
"""

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# SEC requires a real contact address in the User-Agent. Change this to yours.
CONTACT = os.environ.get("SEC_CONTACT", "riccardo.macor@zeefarm.com")
UA = f"edgar-screener (personal research project; {CONTACT})"

BASE = "https://data.sec.gov/api/xbrl/frames/us-gaap"
CACHE = Path(__file__).parent / ".cache"

# Duration tags (income statement) — reported over a period.
# Revenue has several competing tags; we try them in order and take the first hit.
REVENUE_TAGS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
]
EBIT_TAG = "OperatingIncomeLoss"
NET_INCOME_TAG = "NetIncomeLoss"

# Instant tags (balance sheet) — reported at a point in time.
ASSETS_TAG = "Assets"
CURRENT_LIABILITIES_TAG = "LiabilitiesCurrent"
EQUITY_TAG = "StockholdersEquity"


# --------------------------------------------------------------------------
# fetching
# --------------------------------------------------------------------------

def fetch(url, retries=3):
    """GET a URL with SEC-compliant headers, caching the body on disk."""
    CACHE.mkdir(exist_ok=True)
    key = url.replace("https://", "").replace("/", "_").replace("?", "_")
    cached = CACHE / key

    if cached.exists():
        return json.loads(cached.read_text())

    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept-Encoding": "gzip, deflate",
        "Host": "data.sec.gov",
    })

    ctx = ssl.create_default_context()
    last_err = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=45, context=ctx) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    import gzip
                    raw = gzip.decompress(raw)
                body = json.loads(raw.decode("utf-8"))
                cached.write_text(json.dumps(body))
                time.sleep(0.15)   # stay well under SEC's 10 req/s limit
                return body
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None        # this tag/period combination simply has no frame
            last_err = e
            time.sleep(1.5 * (attempt + 1))
        except Exception as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))

    print(f"  ! failed: {url} ({last_err})", file=sys.stderr)
    return None


def frame_duration(tag, year):
    """Annual (period) frame, e.g. CY2024."""
    return fetch(f"{BASE}/{tag}/USD/CY{year}.json")


def frame_instant(tag, year, quarter):
    """Point-in-time frame, e.g. CY2024Q4I."""
    return fetch(f"{BASE}/{tag}/USD/CY{year}Q{quarter}I.json")


def as_map(frame):
    """{cik: entry} from a frame response."""
    if not frame:
        return {}
    return {e["cik"]: e for e in frame.get("data", [])}


def collect_instant(tag, year, verbose=True):
    """
    Balance-sheet items are only published at each company's own year end,
    so a single quarterly frame misses every firm whose fiscal year doesn't
    end in December. We pull all four instants (plus Q4 of the prior year for
    very early fiscal year ends) and keep every observation per company, so
    the caller can pick whichever one sits closest to the income statement.
    """
    out = {}
    frames = [(year - 1, 4), (year, 1), (year, 2), (year, 3), (year, 4)]
    for y, q in frames:
        f = frame_instant(tag, y, q)
        if not f:
            continue
        if verbose:
            print(f"    CY{y}Q{q}I  {len(f.get('data', [])):>6,} filers")
        for e in f.get("data", []):
            out.setdefault(e["cik"], []).append(e)
    return out


def nearest(observations, target_end):
    """Pick the balance-sheet observation closest to the income statement's end date."""
    if not observations:
        return None
    from datetime import date

    def parse(s):
        y, m, d = (int(x) for x in s.split("-"))
        return date(y, m, d)

    try:
        t = parse(target_end)
    except Exception:
        return observations[-1]

    best, best_gap = None, None
    for o in observations:
        try:
            gap = abs((parse(o["end"]) - t).days)
        except Exception:
            continue
        if best_gap is None or gap < best_gap:
            best, best_gap = o, gap
    # more than ~100 days apart means we're looking at the wrong balance sheet
    if best_gap is not None and best_gap > 100:
        return None
    return best


# --------------------------------------------------------------------------
# industry enrichment (optional — one request per company, so it's slow)
# --------------------------------------------------------------------------

def enrich_sic(rows, limit):
    print(f"\nFetching industry codes for the top {limit}...")
    for i, row in enumerate(rows[:limit]):
        cik = str(row["cik"]).zfill(10)
        sub = fetch(f"https://data.sec.gov/submissions/CIK{cik}.json")
        if sub:
            row["sic"] = sub.get("sic")
            row["industry"] = sub.get("sicDescription")
            row["ticker"] = (sub.get("tickers") or [None])[0]
        if (i + 1) % 25 == 0:
            print(f"  {i + 1}/{limit}")
    return rows


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def build(year, min_revenue, verbose=True):
    print(f"Pulling fiscal year {year} from SEC EDGAR\n")

    print("  EBIT (OperatingIncomeLoss)")
    ebit = as_map(frame_duration(EBIT_TAG, year))
    print(f"    {len(ebit):,} filers")

    print("  Revenue")
    revenue = {}
    for tag in REVENUE_TAGS:
        m = as_map(frame_duration(tag, year))
        print(f"    {tag:<55} {len(m):>6,}")
        for cik, e in m.items():
            revenue.setdefault(cik, e)     # first tag wins

    print("  Net income")
    net_income = as_map(frame_duration(NET_INCOME_TAG, year))
    print(f"    {len(net_income):,} filers")

    print("  Total assets")
    assets = collect_instant(ASSETS_TAG, year, verbose)
    print("  Current liabilities")
    curr_liab = collect_instant(CURRENT_LIABILITIES_TAG, year, verbose)
    print("  Equity")
    equity = collect_instant(EQUITY_TAG, year, verbose)

    rows, skipped = [], {"no_revenue": 0, "too_small": 0, "no_balance_sheet": 0,
                         "bad_capital_employed": 0}

    for cik, e in ebit.items():
        rev_entry = revenue.get(cik)
        if not rev_entry:
            skipped["no_revenue"] += 1
            continue

        rev = rev_entry["val"]
        if rev < min_revenue:
            skipped["too_small"] += 1
            continue

        end = e["end"]
        a = nearest(assets.get(cik, []), end)
        cl = nearest(curr_liab.get(cik, []), end)
        if not a or not cl:
            skipped["no_balance_sheet"] += 1
            continue

        capital_employed = a["val"] - cl["val"]
        if capital_employed <= 0:
            # negative capital employed makes ROCE meaningless, not impressive
            skipped["bad_capital_employed"] += 1
            continue

        eq = nearest(equity.get(cik, []), end)
        ni = net_income.get(cik)

        margin = e["val"] / rev
        turnover = rev / capital_employed

        # Data-quality flags. Filers tag XBRL wrong often enough that a screener
        # without these will confidently rank garbage at the top.
        flags = []
        if capital_employed < rev * 0.10:
            # a tiny denominator makes ROCE explode; see Domino's Pizza, ~706%
            flags.append("thin_capital")
        if eq and eq["val"] < 0:
            flags.append("negative_equity")
        if ni and abs(ni["val"]) < 1e5 and rev > 1e9:
            # e.g. net income tagged as 4662 against $33bn of revenue
            flags.append("suspect_units")

        rows.append({
            "flags": flags,
            "cik": cik,
            "name": e["entityName"].strip(),
            "loc": e.get("loc"),
            "fy_end": end,
            "revenue": rev,
            "ebit": e["val"],
            "net_income": ni["val"] if ni else None,
            "assets": a["val"],
            "current_liabilities": cl["val"],
            "equity": eq["val"] if eq else None,
            "capital_employed": capital_employed,
            "operating_margin": margin,
            "capital_turnover": turnover,
            "roce": margin * turnover,
            "net_margin": (ni["val"] / rev) if ni else None,
            "roe": (ni["val"] / eq["val"]) if ni and eq and eq["val"] > 0 else None,
        })

    # Filers sometimes report twice (a parent and its financing subsidiary), which
    # shows up as two entities with identical revenue and identical EBIT.
    sig = {}
    for r in rows:
        sig.setdefault((r["revenue"], r["ebit"]), []).append(r)
    for group in sig.values():
        if len(group) > 1:
            for r in group:
                r["flags"].append("duplicate_filing")

    rows.sort(key=lambda r: r["roce"], reverse=True)

    flagged = sum(1 for r in rows if r["flags"])
    print(f"\n{len(rows):,} companies with a complete picture ({flagged:,} flagged)")
    print("  dropped: " + ", ".join(f"{k}={v:,}" for k, v in skipped.items()))
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--year", type=int, default=2024,
                   help="fiscal year to screen (default: 2024)")
    p.add_argument("--min-revenue", type=float, default=1e9,
                   help="ignore companies below this revenue (default: 1e9)")
    p.add_argument("--out", default="data.json", help="output JSON file")
    p.add_argument("--js", metavar="FILE",
                   help="also write data.js in the shape the web page reads")
    p.add_argument("--enrich-sic", type=int, default=0, metavar="N",
                   help="look up industry codes for the top N (slow: 1 request each)")
    p.add_argument("--top", type=int, default=0,
                   help="print the top N to the terminal as well")
    args = p.parse_args()

    rows = build(args.year, args.min_revenue)

    if args.enrich_sic:
        rows = enrich_sic(rows, args.enrich_sic)

    payload = {
        "generated": time.strftime("%Y-%m-%d %H:%M"),
        "fiscal_year": args.year,
        "min_revenue": args.min_revenue,
        "source": "SEC EDGAR XBRL frames API (us-gaap taxonomy)",
        "definitions": {
            "capital_employed": "total assets - current liabilities",
            "operating_margin": "EBIT / revenue",
            "capital_turnover": "revenue / capital employed",
            "roce": "EBIT / capital employed  ==  operating margin x capital turnover",
        },
        "count": len(rows),
        "companies": rows,
    }
    Path(args.out).write_text(json.dumps(payload, indent=1))
    print(f"\nwrote {args.out}  ({Path(args.out).stat().st_size / 1e6:.1f} MB)")

    if args.js:
        # same shape the web page expects: replace its data.js with this file
        cols = ["name", "loc", "fy_end", "revenue", "ebit", "net_income",
                "assets", "current_liabilities", "equity"]
        compact = [[r[c] for c in cols] for r in rows]
        js = ("window.SCREENER = " + json.dumps({
            "meta": {"fiscal_year": args.year, "min_revenue": args.min_revenue,
                     "generated": time.strftime("%Y-%m-%d"),
                     "universe": len(rows), "included": len(rows),
                     "source": payload["source"]},
            "cols": cols,
            "rows": compact,
        }) + ";\n")
        Path(args.js).write_text(js)
        print(f"wrote {args.js}  ({Path(args.js).stat().st_size / 1e6:.1f} MB) "
              f"- drop this next to index.html")

    if args.top:
        print(f"\n{'company':<42}{'margin':>9}{'turnover':>10}{'ROCE':>9}")
        print("-" * 70)
        for r in rows[:args.top]:
            print(f"{r['name'][:40]:<42}{r['operating_margin']:>8.1%}"
                  f"{r['capital_turnover']:>10.2f}{r['roce']:>9.1%}")


if __name__ == "__main__":
    main()
