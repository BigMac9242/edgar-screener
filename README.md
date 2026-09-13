# The DuPont Plane

A fundamentals screener over US company filings. It ranks filers on the ratios
behind return on capital, and plots them so you can see *how* a company earns
its return rather than only that it does:

```
ROCE = operating margin x capital turnover
     = (EBIT / revenue)  x  (revenue / capital employed)

capital employed = total assets - current liabilities
```

On log axes every company with the same ROCE falls on one straight diagonal.
Visa sits top-left (65.7% margin, 0.53x turnover). Costco sits bottom-right
(3.6% margin, 7.40x turnover). Similar returns, opposite businesses.

## What's here

| File | Does what |
|---|---|
| `screener.py` | Pulls XBRL data from SEC EDGAR, computes the ratios, writes the data files |
| `index.html` | The page — plain HTML, no build step, no framework |
| `data.js` | The dataset the page reads |
| `test_logic.py` | Offline test of the maths against synthetic filings |
| `.github/workflows/refresh.yml` | Weekly job that regenerates `data.js` |

## Running the screener

Standard library only — nothing to install.

```bash
# open screener.py and change CONTACT to your email first (the SEC requires it)
python3 screener.py --year 2024 --min-revenue 1e9 --js data.js --top 40
```

Responses are cached in `.cache/`, so a second run is instant. Useful flags:

- `--year 2023` — screen a different fiscal year
- `--min-revenue 5e8` — widen the universe below $1bn
- `--enrich-sic 100` — look up industry codes for the top 100 (slow, one request each)
- `--out data.json` — the full record, every field, for your own analysis

## Data source

The [SEC EDGAR XBRL frames API](https://www.sec.gov/edgar/sec-api-documentation),
`us-gaap` taxonomy. Free, no key, and it's the same filings a 10-K is built from.
One request returns a single tag across every filer, which is what makes a
whole-market screen cheap.

## Things to be careful about

**The denominator lies more often than the numerator.** Domino's Pizza tops an
unfiltered ranking at ~706% ROCE. Debt-funded buybacks left it with $1.74bn of
assets against $1.61bn of current liabilities, so capital employed collapses to
about $125m and the ratio stops meaning anything. Rows where capital employed is
under a tenth of revenue get a `thin_capital` flag.

**Filers tag XBRL wrong.** Medtronic's net income comes through as `4662` against
$33bn of revenue — a units error in the filing itself, not in this code. Caught by
the `suspect_units` flag.

**The same company can appear twice.** Charter and CCO Holdings file separately
with identical figures. Flagged as `duplicate_filing`.

**Fiscal years don't line up.** The `CY2024` frame holds NVIDIA's year ending
January 2025 next to Apple's ending September 2024. They aren't the same economy.

**US filers only.** EDGAR holds US filings, so no Volkswagen, no LVMH, no Toyota.

## Deploying

It's a static site. Push to GitHub, then Settings -> Pages -> deploy from `main`,
folder `/ (root)`.

For the weekly refresh to work, add a repository secret named `SEC_CONTACT`
holding your email address: Settings -> Secrets and variables -> Actions.
