"""Macro risk appetite score built from FRED data.

Returns a value in [0, 1]:
    0 -> super conservative (bonds / money market)
    1 -> super aggressive (stocks / ETFs)

Each indicator is scored on two things, both measured against its own history
over a lookback window (default 10 years):
    level - percentile rank of the latest value
    trend - percentile rank of the latest N-month change (default 6 months)
The indicator score is a blend of the two, flipped when a higher reading means
more risk. The final score is a weighted average of the indicator scores.
"""

import csv
import io
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from dotenv import load_dotenv

load_dotenv()

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
FRED_API_URL = "https://api.stlouisfed.org/fred/series/observations"

DEFAULT_LOOKBACK_YEARS = 10
DEFAULT_TREND_MONTHS = 6
LEVEL_WEIGHT = 0.6
TREND_WEIGHT = 0.4
CACHE_TTL_SECONDS = 6 * 60 * 60
HTTP_TIMEOUT_SECONDS = 20

# Raw FRED series to download.
SERIES = {
    "T10Y2Y": "10Y minus 2Y Treasury spread",
    "BAA10Y": "Moody's Baa corporate yield minus 10Y Treasury",
    "VIXCLS": "CBOE Volatility Index (VIX)",
    "NFCI": "Chicago Fed National Financial Conditions Index",
    "UNRATE": "Unemployment rate",
    "DFF": "Effective federal funds rate",
    "CPIAUCSL": "Consumer Price Index (all urban)",
    "INDPRO": "Industrial production index",
}

# direction=+1: higher reading is risk-on (more aggressive).
# direction=-1: higher reading is risk-off (more conservative).
INDICATORS = [
    {
        "key": "yield_curve",
        "name": "Yield curve (10Y-2Y)",
        "series": ["T10Y2Y"],
        "direction": 1,
        "weight": 0.15,
        "unit": "pct pts",
        "why": "A steep curve signals expected growth; inversion has preceded most US recessions.",
    },
    {
        "key": "credit_spread",
        "name": "Corporate credit spread (Baa-10Y)",
        "series": ["BAA10Y"],
        "direction": -1,
        "weight": 0.20,
        "unit": "pct pts",
        "why": "Widening spreads mean lenders demand more compensation for default risk.",
    },
    {
        "key": "volatility",
        "name": "Equity volatility (VIX)",
        "series": ["VIXCLS"],
        "direction": -1,
        "weight": 0.15,
        "unit": "index",
        "why": "High expected volatility reflects market stress and drawdown risk.",
    },
    {
        "key": "financial_conditions",
        "name": "Financial conditions (NFCI)",
        "series": ["NFCI"],
        "direction": -1,
        "weight": 0.15,
        "unit": "index",
        "why": "Positive values mean tighter-than-average credit, funding and leverage conditions.",
    },
    {
        "key": "labor_market",
        "name": "Unemployment momentum (Sahm gap)",
        "series": ["UNRATE"],
        "direction": -1,
        "weight": 0.15,
        "unit": "pct pts",
        "why": "A 3-month average unemployment rate rising 0.5pt above its 12-month low has signalled recessions.",
    },
    {
        "key": "real_policy_rate",
        "name": "Real fed funds rate",
        "series": ["DFF", "CPIAUCSL"],
        "direction": -1,
        "weight": 0.10,
        "unit": "%",
        "why": "High real rates tighten the economy and make cash and bonds more attractive.",
    },
    {
        "key": "growth",
        "name": "Industrial production growth (YoY)",
        "series": ["INDPRO"],
        "direction": 1,
        "weight": 0.10,
        "unit": "%",
        "why": "Expanding output supports corporate earnings.",
    },
]

BANDS = [
    (0.2, "Very conservative", "Mostly money market and short-term Treasuries."),
    (0.4, "Conservative", "Bond-heavy with a small equity sleeve."),
    (0.6, "Balanced", "A roughly even mix of bonds and equities."),
    (0.8, "Growth", "Equity-heavy with some bonds for ballast."),
    (1.01, "Aggressive", "Mostly stocks and equity ETFs."),
]

_cache = {}
_cache_lock = threading.Lock()


# ---------- FRED download ----------

def _http_get(url):
    req = Request(url, headers={"User-Agent": "ms-hack-risk/1.0"})
    with urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
        return resp.read().decode("utf-8")


def fetch_series(series_id, start):
    """Return [(date, float)] for a FRED series from `start` onwards."""
    api_key = os.environ.get("FRED_API_KEY")
    if api_key:
        query = urlencode({
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "observation_start": start.isoformat(),
        })
        rows = json.loads(_http_get(f"{FRED_API_URL}?{query}"))["observations"]
        pairs = ((r["date"], r["value"]) for r in rows)
    else:
        query = urlencode({"id": series_id, "cosd": start.isoformat()})
        reader = csv.reader(io.StringIO(_http_get(f"{FRED_CSV_URL}?{query}")))
        next(reader)  # header
        pairs = ((r[0], r[1]) for r in reader if len(r) >= 2)

    out = []
    for d, v in pairs:
        if v in (".", ""):  # FRED marks missing observations with "."
            continue
        out.append((date.fromisoformat(d), float(v)))
    return out


# ---------- Series helpers ----------
# Monthly series are lists of ((year, month), value) sorted by month.

def to_monthly(obs):
    """Average observations within each calendar month."""
    buckets = {}
    for d, v in obs:
        buckets.setdefault((d.year, d.month), []).append(v)
    return [(k, sum(vs) / len(vs)) for k, vs in sorted(buckets.items())]


def yoy_pct(monthly):
    by_month = dict(monthly)
    out = []
    for (y, m), v in monthly:
        prev = by_month.get((y - 1, m))
        if prev:
            out.append(((y, m), (v / prev - 1) * 100))
    return out


def sahm_gap(monthly):
    """3-month average minus the lowest 3-month average of the prior 12 months."""
    vals = [v for _, v in monthly]
    avg3 = [sum(vals[i - 2:i + 1]) / 3 for i in range(2, len(vals))]
    keys = [k for k, _ in monthly][2:]
    return [(keys[i], avg3[i] - min(avg3[i - 12:i])) for i in range(12, len(avg3))]


def subtract_ffill(a, b):
    """a - b by month, carrying b's last value forward (CPI is released with a lag)."""
    b_map = dict(b)
    out, last_b = [], None
    for k, v in a:
        last_b = b_map.get(k, last_b)
        if last_b is not None:
            out.append((k, v - last_b))
    return out


def build_indicator_series(ind, raw):
    key = ind["key"]
    if key == "labor_market":
        return sahm_gap(to_monthly(raw["UNRATE"]))
    if key == "real_policy_rate":
        return subtract_ffill(to_monthly(raw["DFF"]), yoy_pct(to_monthly(raw["CPIAUCSL"])))
    if key == "growth":
        return yoy_pct(to_monthly(raw["INDPRO"]))
    return to_monthly(raw[ind["series"][0]])


# ---------- Scoring ----------

def percentile_rank(values, x):
    """Fraction of `values` below x, counting ties as half (mid-rank)."""
    below = sum(1 for v in values if v < x)
    equal = sum(1 for v in values if v == x)
    return (below + 0.5 * equal) / len(values)


def score_indicator(series, lookback_months, trend_months, direction):
    if len(series) < trend_months + 24:
        raise ValueError("not enough history")

    window = series[-lookback_months:]
    values = [v for _, v in window]
    current = values[-1]

    all_vals = [v for _, v in series]
    changes = [all_vals[i] - all_vals[i - trend_months] for i in range(trend_months, len(all_vals))]
    changes = changes[-lookback_months:]
    change = changes[-1]

    level = percentile_rank(values, current)
    trend = percentile_rank(changes, change)
    if direction < 0:
        level, trend = 1 - level, 1 - trend

    return {
        "value": round(current, 3),
        "change": round(change, 3),
        "level_score": round(level, 3),
        "trend_score": round(trend, 3),
        "score": LEVEL_WEIGHT * level + TREND_WEIGHT * trend,
        "window_min": round(min(values), 3),
        "window_max": round(max(values), 3),
    }


def band_for(score):
    for upper, label, guidance in BANDS:
        if score < upper:
            return label, guidance
    return BANDS[-1][1], BANDS[-1][2]


def compute_risk_score(lookback_years=DEFAULT_LOOKBACK_YEARS, trend_months=DEFAULT_TREND_MONTHS):
    cache_key = (lookback_years, trend_months)
    with _cache_lock:
        hit = _cache.get(cache_key)
        if hit and time.time() - hit[0] < CACHE_TTL_SECONDS:
            return hit[1]

    today = date.today()
    # Extra years cover YoY transforms, the Sahm 12-month window and the trend lag.
    start = date(today.year - lookback_years - 3, 1, 1)

    with ThreadPoolExecutor(max_workers=len(SERIES)) as pool:
        futures = {sid: pool.submit(fetch_series, sid, start) for sid in SERIES}
    raw, errors = {}, {}
    for sid, fut in futures.items():
        try:
            raw[sid] = fut.result()
        except Exception as exc:  # one bad series should not sink the whole score
            errors[sid] = str(exc)

    lookback_months = lookback_years * 12
    components, total, weight_used = [], 0.0, 0.0
    for ind in INDICATORS:
        missing = [s for s in ind["series"] if s not in raw]
        if missing:
            errors[ind["key"]] = f"missing series: {', '.join(missing)}"
            continue
        try:
            series = build_indicator_series(ind, raw)
            result = score_indicator(series, lookback_months, trend_months, ind["direction"])
        except ValueError as exc:
            errors[ind["key"]] = str(exc)
            continue

        y, m = series[-1][0]
        total += ind["weight"] * result["score"]
        weight_used += ind["weight"]
        components.append({
            "key": ind["key"],
            "name": ind["name"],
            "series": ind["series"],
            "unit": ind["unit"],
            "direction": "risk-on when higher" if ind["direction"] > 0 else "risk-off when higher",
            "weight": ind["weight"],
            "latest_month": f"{y:04d}-{m:02d}",
            "latest_observation": max(raw[s][-1][0] for s in ind["series"]).isoformat(),
            **result,
            "score": round(result["score"], 3),
            "why": ind["why"],
        })

    if not components:
        raise RuntimeError(f"No indicators could be computed: {errors}")

    score = total / weight_used
    label, guidance = band_for(score)
    payload = {
        "score": round(score, 3),
        "label": label,
        "guidance": guidance,
        "suggested_allocation": {
            "equities": round(score, 2),
            "bonds_and_cash": round(1 - score, 2),
        },
        "as_of": today.isoformat(),
        "method": {
            "lookback_years": lookback_years,
            "trend_months": trend_months,
            "level_weight": LEVEL_WEIGHT,
            "trend_weight": TREND_WEIGHT,
            "coverage": round(weight_used, 3),
            "description": (
                "Each indicator is ranked against its own history over the lookback window "
                "(level) and by its recent change (trend). Scores are oriented so 1 = risk-on, "
                "then combined by weight."
            ),
        },
        "components": components,
        "errors": errors,
        "source": "Federal Reserve Bank of St. Louis, FRED",
    }

    with _cache_lock:
        _cache[cache_key] = (time.time(), payload)
    return payload


if __name__ == "__main__":
    print(json.dumps(compute_risk_score(), indent=2))
