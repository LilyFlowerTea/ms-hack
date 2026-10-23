"""Largest US assets with prices, news and analyst ratings over a time horizon.

One call returns every asset class, each ranked by size:
    stocks       - top 100 on major US exchanges by market cap
    etfs         - top 50 non-bond, non-cash ETFs (equity, gold, ...) by net assets
    bonds        - top 50 bond ETFs by net assets
    money_market - top 25 ultrashort bond ETFs (T-bill / money-market-style)

For the last N days each asset gets:
    price   - change, high, low and volatility over the horizon (Yahoo)
    ratings - analyst consensus and every upgrade/downgrade (Yahoo, stocks only)
    news    - company news (Finnhub, stocks only)

Data sources:
    Yahoo Finance (yfinance) for rankings, prices and analyst actions, since
    Finnhub's free plan has no screener, fund sizes or price history.
    Finnhub for news. Its free plan allows 60 calls/minute, so each stock's
    news is fetched once for the past year, cached, and filtered locally. The
    first call after startup can take about a minute while news loads.

Money market mutual funds (VMFXX, SPAXX, ...) are left out because Yahoo's
fund sizes for them are unreliable, e.g. Fidelity's funds report $1M.
"""

import json
import math
import os
import threading
import time
import warnings
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import yfinance as yf
from dotenv import load_dotenv
from yfinance import EquityQuery, ETFQuery

load_dotenv()
# yfinance trips pandas deprecation warnings on every analyst-data call.
warnings.filterwarnings("ignore", module="yfinance")

COUNTS = {"stocks": 100, "etfs": 50, "bonds": 50, "money_market": 25}
DEFAULT_HORIZON_DAYS = 30
DEFAULT_NEWS_LIMIT = 5
MAX_HORIZON_DAYS = 365

LIST_TTL_SECONDS = 6 * 60 * 60
PRICE_TTL_SECONDS = 15 * 60
RATINGS_TTL_SECONDS = 6 * 60 * 60
NEWS_TTL_SECONDS = 60 * 60
HTTP_TIMEOUT_SECONDS = 20

# NASDAQ tiers, NYSE and NYSE American. Excludes OTC listings of foreign shares.
STOCK_EXCHANGES = ["NMS", "NGM", "NCM", "NYQ", "ASE"]
# Fetch extra stocks so share-class duplicates (GOOG/GOOGL) can be dropped.
STOCK_OVERFETCH = 50
SCREENER_MAX = 250

# Morningstar categories as Yahoo uses them. yfinance's own list is out of date
# (it lacks "Intermediate Core Bond", home of BND and AGG), so names are not
# validated; unknown names simply match nothing.
BOND_CATEGORIES = [
    "Intermediate Core Bond", "Intermediate Core-Plus Bond", "Intermediate-Term Bond",
    "Short-Term Bond", "Long-Term Bond", "Short Government", "Intermediate Government",
    "Long Government", "Inflation-Protected Bond", "Short-Term Inflation-Protected Bond",
    "Corporate Bond", "Long-Term Corporate Bond", "High Yield Bond", "Bank Loan",
    "Multisector Bond", "Nontraditional Bond", "Securitized Bond - Diversified",
    "Securitized Bond - Focused", "Target Maturity", "World Bond", "World Bond-USD Hedged",
    "Global Bond-USD Hedged", "Emerging Markets Bond", "Emerging-Markets Local-Currency Bond",
    "Muni National Interm", "Muni National Long", "Muni National Short", "Muni Target Maturity",
    "High Yield Muni", "Muni California Intermediate", "Muni California Long",
    "Muni New York Intermediate", "Muni New York Long", "Muni Single State Interm",
    "Muni Single State Long", "Muni Single State Short",
]
MONEY_MARKET_CATEGORIES = ["Ultrashort Bond"]

FINNHUB_API_URL = "https://finnhub.io/api/v1"
FINNHUB_CALLS_PER_MINUTE = 55  # free plan allows 60; leave headroom


class _TTLCache:
    def __init__(self):
        self._data = {}
        self._lock = threading.Lock()

    def get(self, key, ttl, allow_stale=False):
        with self._lock:
            hit = self._data.get(key)
        if not hit:
            return None
        if time.time() - hit[0] < ttl or allow_stale:
            return hit[1]
        return None

    def age(self, key):
        with self._lock:
            hit = self._data.get(key)
        return time.time() - hit[0] if hit else None

    def set(self, key, value):
        with self._lock:
            self._data[key] = (time.time(), value)


_lists = _TTLCache()
_prices = _TTLCache()
_ratings = _TTLCache()
_news = _TTLCache()
_payloads = _TTLCache()


# ---------- Rankings (Yahoo screener) ----------

class _UncheckedETFQuery(ETFQuery):
    """ETFQuery that skips yfinance's stale category validation."""

    def _validate_eq_operand(self, operand):
        pass

    def _validate_isin_operand(self, operand):
        pass


def _screen_etfs(categories=None):
    query = _UncheckedETFQuery("eq", ["region", "us"])
    if categories:
        query = _UncheckedETFQuery("and", [query, _UncheckedETFQuery("is-in", ["categoryname", *categories])])
    return yf.screen(query, sortField="fundnetassets", sortAsc=False, size=SCREENER_MAX)["quotes"]


def _screen_stocks():
    query = EquityQuery("and", [
        EquityQuery("eq", ["region", "us"]),
        EquityQuery("is-in", ["exchange", *STOCK_EXCHANGES]),
    ])
    size = COUNTS["stocks"] + STOCK_OVERFETCH
    return yf.screen(query, sortField="intradaymarketcap", sortAsc=False, size=size)["quotes"]


def _entry(q, asset_class):
    is_stock = asset_class == "stocks"
    entry = {
        "symbol": q["symbol"],
        "name": q.get("longName") or q.get("shortName"),
        "asset_class": asset_class,
        "exchange": q.get("fullExchangeName") or q.get("exchange"),
        "currency": q.get("currency"),
        "size_usd": q.get("marketCap") if is_stock else q.get("netAssets"),
        "size_type": "market_cap" if is_stock else "net_assets",
        "dividend_yield_pct": q.get("dividendYield"),
        "_quote": {
            "current": q.get("regularMarketPrice"),
            "day_change_pct": q.get("regularMarketChangePercent"),
        },
    }
    if is_stock:
        entry["pe_ratio"] = q.get("trailingPE")
        entry["_consensus"] = q.get("averageAnalystRating")
    else:
        entry["expense_ratio_pct"] = q.get("netExpenseRatio")
    return entry


def get_universe():
    """{asset_class: [entry, ...]}, ranked by size within each class."""
    cached = _lists.get("all", LIST_TTL_SECONDS)
    if cached:
        return cached

    with ThreadPoolExecutor(max_workers=4) as pool:
        stocks_f = pool.submit(_screen_stocks)
        all_etfs_f = pool.submit(_screen_etfs)
        bonds_f = pool.submit(_screen_etfs, BOND_CATEGORIES)
        mm_f = pool.submit(_screen_etfs, MONEY_MARKET_CATEGORIES)

    stocks, seen = [], set()
    for q in stocks_f.result():
        name = q.get("longName") or q.get("shortName") or q["symbol"]
        if name in seen:  # keep the larger share class only
            continue
        seen.add(name)
        stocks.append(q)

    bonds, money_market = bonds_f.result(), mm_f.result()
    fixed_income = {q["symbol"] for q in bonds + money_market}
    etfs = [q for q in all_etfs_f.result() if q["symbol"] not in fixed_income]

    universe = {}
    for asset_class, quotes in (("stocks", stocks), ("etfs", etfs), ("bonds", bonds), ("money_market", money_market)):
        entries = [_entry(q, asset_class) for q in quotes[:COUNTS[asset_class]]]
        for rank, e in enumerate(entries, 1):
            e["rank"] = rank
        universe[asset_class] = entries

    _lists.set("all", universe)
    return universe


# ---------- Prices over the horizon (Yahoo) ----------

def _download_history(symbols):
    """A year of daily prices for every symbol, shared by all horizons."""
    key = tuple(symbols)
    cached = _prices.get(key, PRICE_TTL_SECONDS)
    if cached is not None:
        return cached
    # A week of padding gives a baseline close when the horizon starts on a holiday.
    start = date.today() - timedelta(days=MAX_HORIZON_DAYS + 7)
    df = yf.download(
        symbols, start=start.isoformat(), interval="1d",
        auto_adjust=True, progress=False, threads=True, group_by="ticker",
    )
    _prices.set(key, df)
    return df


def _price_summary(df, symbol, start, quote):
    out = {**quote, "horizon_start_price": None, "horizon_change_pct": None,
           "horizon_high": None, "horizon_low": None, "volatility_pct": None}
    if symbol not in df.columns.get_level_values(0):
        return out
    hist = df[symbol].dropna(subset=["Close"])
    if hist.empty:
        return out

    before = hist[hist.index.date <= start]
    baseline = float(before["Close"].iloc[-1] if len(before) else hist["Close"].iloc[0])
    window = hist[hist.index.date > start]
    if window.empty:
        window = hist.tail(1)
    closes = [baseline] + [float(c) for c in window["Close"]]
    end = closes[-1]

    returns = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1]]
    vol = None
    if len(returns) >= 2:
        mean = sum(returns) / len(returns)
        var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        vol = round(math.sqrt(var) * math.sqrt(252) * 100, 2)  # annualised

    out.update({
        "horizon_start_price": round(baseline, 4),
        "horizon_change_pct": round((end / baseline - 1) * 100, 2) if baseline else None,
        "horizon_high": round(float(window["High"].max()), 4),
        "horizon_low": round(float(window["Low"].min()), 4),
        "volatility_pct": vol,
    })
    return out


# ---------- Analyst ratings (Yahoo, stocks only) ----------

def _positive(v):
    """Price targets come back as 0 or NaN when absent."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if v > 0 and not math.isnan(v) else None


def _analyst_actions(symbol):
    cached = _ratings.get(symbol, RATINGS_TTL_SECONDS)
    if cached is not None:
        return cached
    df = yf.Ticker(symbol).upgrades_downgrades
    actions = []
    if df is not None and not df.empty:
        cutoff = datetime.now() - timedelta(days=MAX_HORIZON_DAYS + 1)
        for ts, row in df[df.index >= cutoff].iterrows():
            actions.append({
                "date": ts.date().isoformat(),
                "firm": row.get("Firm"),
                "action": row.get("Action"),
                "from_grade": row.get("FromGrade") or None,
                "to_grade": row.get("ToGrade") or None,
                "price_target": _positive(row.get("currentPriceTarget")),
                "prior_price_target": _positive(row.get("priorPriceTarget")),
            })
    _ratings.set(symbol, actions)
    return actions


def _ratings_summary(actions, start, consensus):
    in_horizon = [a for a in actions if a["date"] > start.isoformat()]
    targets = [a["price_target"] for a in in_horizon if a["price_target"]]
    return {
        "consensus": consensus,  # Yahoo scale: 1 = strong buy, 5 = strong sell
        "upgrades": sum(a["action"] == "up" for a in in_horizon),
        "downgrades": sum(a["action"] == "down" for a in in_horizon),
        "initiations": sum(a["action"] == "init" for a in in_horizon),
        "avg_price_target": round(sum(targets) / len(targets), 2) if targets else None,
        "actions": in_horizon,
    }


# ---------- News (Finnhub, stocks only) ----------

class _RateLimiter:
    def __init__(self, calls, period):
        self.calls, self.period = calls, period
        self._stamps = deque()
        self._lock = threading.Lock()

    def wait(self):
        while True:
            with self._lock:
                now = time.time()
                while self._stamps and now - self._stamps[0] >= self.period:
                    self._stamps.popleft()
                if len(self._stamps) < self.calls:
                    self._stamps.append(now)
                    return
                delay = self.period - (now - self._stamps[0])
            time.sleep(delay)


_finnhub_limiter = _RateLimiter(FINNHUB_CALLS_PER_MINUTE, 60)
_refresh_lock = threading.Lock()


def _finnhub(path, **params):
    api_key = os.environ.get("FINNHUB_API_KEY")
    if not api_key:
        raise RuntimeError("FINNHUB_API_KEY is not set")
    _finnhub_limiter.wait()
    # Send the token as a header so it never appears in URLs or logs.
    req = Request(
        f"{FINNHUB_API_URL}/{path}?{urlencode(params)}",
        headers={"X-Finnhub-Token": api_key, "User-Agent": "ms-hack-assets/1.0"},
    )
    try:
        with urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        reasons = {401: "rejected the API key", 403: "plan does not cover this symbol", 429: "rate limit reached"}
        raise RuntimeError(f"Finnhub {reasons.get(exc.code, f'returned HTTP {exc.code}')}")


def _fetch_news(symbol):
    # Fetch the past year once; Finnhub returns at most ~250 newest articles.
    end = date.today()
    items = _finnhub("company-news", symbol=symbol,
                     **{"from": (end - timedelta(days=MAX_HORIZON_DAYS)).isoformat(), "to": end.isoformat()})
    articles = sorted(
        (
            {
                "published": datetime.fromtimestamp(n["datetime"], tz=timezone.utc).isoformat(),
                "headline": n.get("headline"),
                "summary": n.get("summary"),
                "source": n.get("source"),
                "url": n.get("url"),
            }
            for n in items if n.get("datetime")
        ),
        key=lambda a: a["published"], reverse=True,
    )
    _news.set(symbol, articles)
    return articles


def _refresh_stale_news(symbols):
    if not _refresh_lock.acquire(blocking=False):
        return  # a refresh is already running
    try:
        for s in symbols:
            try:
                _fetch_news(s)
            except Exception:
                pass  # keep serving the stale copy
    finally:
        _refresh_lock.release()


def _load_news(symbols):
    """Fetch missing news now; refresh expired news in the background."""
    missing = [s for s in symbols if _news.get(s, 0, allow_stale=True) is None]
    stale = [s for s in symbols if s not in missing and _news.get(s, NEWS_TTL_SECONDS) is None]

    errors = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {s: pool.submit(_fetch_news, s) for s in missing}
    for s, fut in futures.items():
        try:
            fut.result()
        except Exception as exc:
            errors[s] = str(exc)
    if stale:
        threading.Thread(target=_refresh_stale_news, args=(stale,), daemon=True).start()
    return errors


def _news_summary(symbol, start, limit):
    articles = _news.get(symbol, 0, allow_stale=True)
    if articles is None:
        return None
    since = start.isoformat()
    in_horizon = [a for a in articles if a["published"][:10] > since]
    return {
        "count": len(in_horizon),
        # Finnhub caps each fetch at ~250 articles, so busy tickers may not reach back to `start`.
        "covers_from": articles[-1]["published"][:10] if articles else None,
        "articles": in_horizon[:limit],
    }


# ---------- Combined ----------

def get_assets(days=DEFAULT_HORIZON_DAYS, news_limit=DEFAULT_NEWS_LIMIT):
    cache_key = (days, news_limit)
    cached = _payloads.get(cache_key, PRICE_TTL_SECONDS)
    if cached:
        return cached

    today = date.today()
    start = today - timedelta(days=days)
    universe = get_universe()
    all_entries = [e for entries in universe.values() for e in entries]
    stock_symbols = [e["symbol"] for e in universe["stocks"]]

    errors = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        history_f = pool.submit(_download_history, sorted(e["symbol"] for e in all_entries))
        news_f = pool.submit(_load_news, stock_symbols)
        with ThreadPoolExecutor(max_workers=10) as rpool:
            ratings_f = {s: rpool.submit(_analyst_actions, s) for s in stock_symbols}

    try:
        history = history_f.result()
    except Exception as exc:
        history = None
        errors["prices"] = str(exc)
    errors.update({f"news:{s}": msg for s, msg in news_f.result().items()})

    out = {}
    for asset_class, entries in universe.items():
        rows = []
        for e in entries:
            row = {k: v for k, v in e.items() if not k.startswith("_")}
            row["price"] = _price_summary(history, e["symbol"], start, e["_quote"]) if history is not None else e["_quote"]
            if asset_class == "stocks":
                try:
                    row["ratings"] = _ratings_summary(ratings_f[e["symbol"]].result(), start, e["_consensus"])
                except Exception as exc:
                    row["ratings"] = None
                    errors[f"ratings:{e['symbol']}"] = str(exc)
                row["news"] = _news_summary(e["symbol"], start, news_limit)
            else:
                row["ratings"] = None
                row["news"] = None
            rows.append(row)
        out[asset_class] = rows

    payload = {
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "horizon": {"days": days, "from": start.isoformat(), "to": today.isoformat()},
        "counts": {k: len(v) for k, v in out.items()},
        "assets": out,
        "errors": errors,
        "sources": {
            "rankings_prices_ratings": "Yahoo Finance",
            "news": "Finnhub",
        },
    }
    _payloads.set(cache_key, payload)
    return payload


def prewarm():
    """Load rankings, ratings and news in the background so the first call is fast."""
    def run():
        try:
            get_assets()
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()


if __name__ == "__main__":
    print(json.dumps(get_assets(), indent=2)[:5000])
