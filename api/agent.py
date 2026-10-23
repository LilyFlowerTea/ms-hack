"""LLM portfolio agent built on the macro risk score and assets APIs.

The agent loads the macro risk score (FRED-based, 0 = conservative,
1 = aggressive) and the assets API, trims them, and hands both to the model
with the user's prompt. The model picks holdings and may nudge the risk scale for the user's situation. The
code then enforces the rules so the output is always well-formed:
    - the applied risk scale stays within MAX_RISK_ADJUSTMENT of the macro score
    - each class has exactly PICKS[class] holdings drawn from the assets API
    - class weights come from the risk scale (see class_weights)
"""

import json
import os

from dotenv import load_dotenv
from openai import AzureOpenAI

try:
    from api import assets
    from api.risk import band_for, compute_risk_score
except ImportError:  # running from inside api/
    import assets
    from risk import band_for, compute_risk_score

load_dotenv()

ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT", "https://app-uoaihack2ix6ak.azurewebsites.net")
API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
MODEL_NAME = os.environ.get("AZURE_OPENAI_MODEL", "gpt-6.1-sol")
MAX_COMPLETION_TOKENS = 16384

PICKS = {"stocks": 20, "etfs": 10, "bonds": 10, "money_market": 5}
MAX_RISK_ADJUSTMENT = 0.2
STOCK_SHARE_OF_EQUITIES = 0.7
HEADLINES_PER_STOCK = 3

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = AzureOpenAI(
            api_version=API_VERSION,
            azure_endpoint=ENDPOINT,
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
        )
    return _client


# ---------- Risk scale -> class weights ----------

def class_weights(risk):
    """Equities get `risk`, fixed income the rest; cash grows as risk falls."""
    fixed = 1 - risk
    cash_share = 0.2 + 0.5 * fixed  # 70% of fixed income at risk 0, 20% at risk 1
    return {
        "stocks": risk * STOCK_SHARE_OF_EQUITIES,
        "etfs": risk * (1 - STOCK_SHARE_OF_EQUITIES),
        "bonds": fixed * (1 - cash_share),
        "money_market": fixed * cash_share,
    }


# ---------- Model context ----------

def _billions(v):
    return round(v / 1e9, 1) if v else None


def _compact_asset(row):
    price = row.get("price") or {}
    out = {
        "symbol": row["symbol"],
        "name": row["name"],
        "rank": row["rank"],
        "size_usd_bn": _billions(row.get("size_usd")),
        "dividend_yield_pct": row.get("dividend_yield_pct"),
        "price": price.get("current"),
        "horizon_change_pct": price.get("horizon_change_pct"),
        "volatility_pct": price.get("volatility_pct"),
    }
    if row["asset_class"] != "stocks":
        out["expense_ratio_pct"] = row.get("expense_ratio_pct")
        return out

    out["pe_ratio"] = row.get("pe_ratio")
    ratings = row.get("ratings") or {}
    target = ratings.get("avg_price_target")
    out.update({
        "consensus": ratings.get("consensus"),
        "upgrades": ratings.get("upgrades"),
        "downgrades": ratings.get("downgrades"),
        "target_upside_pct": round((target / price["current"] - 1) * 100, 1)
        if target and price.get("current") else None,
    })
    news = row.get("news") or {}
    out["news_count"] = news.get("count")
    out["headlines"] = [a["headline"] for a in news.get("articles", [])[:HEADLINES_PER_STOCK]]
    return out


def macro_risk_context(macro):
    return {
        "score": macro["score"],
        "label": macro["label"],
        "guidance": macro["guidance"],
        "as_of": macro["as_of"],
        "components": [
            {k: c[k] for k in ("name", "value", "change", "score", "direction", "weight", "latest_month")}
            for c in macro["components"]
        ],
    }


def assets_context(data):
    return {
        "horizon": data["horizon"],
        "assets": {c: [_compact_asset(r) for r in data["assets"][c]] for c in PICKS},
    }


SYSTEM_PROMPT = f"""You are a portfolio allocation agent.

The user message contains their request, the macro risk score (FRED data: 0 = very conservative, 1 = very aggressive) and the largest US assets in each class from the assets API. Stocks include P/E, analyst consensus (1 = strong buy, 5 = strong sell), upgrades, downgrades, price-target upside and recent headlines.

1. Choose exactly {PICKS['stocks']} stocks, {PICKS['etfs']} ETFs, {PICKS['bonds']} bonds and {PICKS['money_market']} money market funds. Only use symbols listed for that class.
2. Set risk_scale (0-1). Start from the macro score and adjust by at most {MAX_RISK_ADJUSTMENT} for what the user says (age, horizon, goals, risk tolerance). Without such details, use the macro score.

Class weights follow from risk_scale: equities get risk_scale ({STOCK_SHARE_OF_EQUITIES:.0%} stocks, {1 - STOCK_SHARE_OF_EQUITIES:.0%} ETFs) and fixed income gets the rest, with more in money market as risk_scale falls. You only choose holdings and their relative weights within each class.

Apply the risk scale to your picks too: a low scale favours low volatility, dividends, short duration and quality; a high scale allows more growth, momentum and longer duration.

Reply with only a JSON object:
{{
  "risk_scale": <number>,
  "risk_adjustment_reason": "<why risk_scale differs from the macro score, or empty>",
  "reasoning": "<a few paragraphs: macro view, how the user's prompt shaped the portfolio, sector and duration choices, key risks>",
  "holdings": {{
    "stocks": [{{"symbol": "...", "weight": <relative weight > 0>, "rationale": "<one sentence>"}}],
    "etfs": [...],
    "bonds": [...],
    "money_market": [...]
  }}
}}"""


# ---------- LLM call ----------

def _parse_json(text):
    text = (text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("model did not return JSON")
    return json.loads(text[start:end + 1])


def _chat(prompt, macro, universe):
    # The portal only offers /chat/completions, where gpt-6.1-sol cannot use
    # function tools, so the API data goes into the prompt instead.
    context = {"macro_risk": macro_risk_context(macro), **assets_context(universe)}
    response = _get_client().chat.completions.create(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Request: {prompt}\n\nData:\n{json.dumps(context, default=str)}"},
        ],
        max_completion_tokens=MAX_COMPLETION_TOKENS,
        model=MODEL_NAME,
    )
    return _parse_json(response.choices[0].message.content)


# ---------- Enforcing the rules ----------

def _positive_float(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _build_class(picks, universe_rows, count, class_weight):
    """Keep valid picks, top up from the largest assets, then scale weights to the class."""
    by_symbol = {r["symbol"]: r for r in universe_rows}
    chosen, notes = [], []
    for p in picks or []:
        symbol = str(p.get("symbol", "")).upper()
        if symbol not in by_symbol:
            notes.append(f"dropped {symbol or '?'}: not in this asset class")
        elif symbol in {c["symbol"] for c in chosen}:
            notes.append(f"dropped duplicate {symbol}")
        elif len(chosen) < count:
            chosen.append({"symbol": symbol, "weight": _positive_float(p.get("weight")),
                           "rationale": p.get("rationale") or ""})

    if len(chosen) < count:
        taken = {c["symbol"] for c in chosen}
        fill = [r for r in universe_rows if r["symbol"] not in taken][:count - len(chosen)]
        if fill:
            notes.append(f"added {', '.join(r['symbol'] for r in fill)} by size to reach {count}")
        chosen += [{"symbol": r["symbol"], "weight": None, "rationale": "Added by size to complete the selection."}
                   for r in fill]

    # Missing or invalid weights default to the average of the valid ones.
    valid = [c["weight"] for c in chosen if c["weight"]]
    default = sum(valid) / len(valid) if valid else 1.0
    total = sum(c["weight"] or default for c in chosen)

    holdings = []
    for c in chosen:
        row = by_symbol[c["symbol"]]
        price = row.get("price") or {}
        share = (c["weight"] or default) / total
        holdings.append({
            "symbol": c["symbol"],
            "name": row["name"],
            "weight_pct": round(share * class_weight * 100, 2),
            "weight_in_class_pct": round(share * 100, 2),
            "price": price.get("current"),
            "horizon_change_pct": price.get("horizon_change_pct"),
            "volatility_pct": price.get("volatility_pct"),
            "rationale": c["rationale"],
        })
    holdings.sort(key=lambda h: h["weight_pct"], reverse=True)
    return holdings, notes


def run_agent(prompt):
    macro = compute_risk_score()
    universe = assets.get_assets(assets.DEFAULT_HORIZON_DAYS, HEADLINES_PER_STOCK)
    answer = _chat(prompt, macro, universe)

    macro_score = macro["score"]
    proposed = _positive_float(answer.get("risk_scale"))
    proposed = macro_score if proposed is None else proposed
    lo, hi = max(0.0, macro_score - MAX_RISK_ADJUSTMENT), min(1.0, macro_score + MAX_RISK_ADJUSTMENT)
    applied = round(min(max(proposed, lo), hi), 3)
    label, guidance = band_for(applied)

    weights = class_weights(applied)
    picks = answer.get("holdings") or {}
    allocation, notes = {}, {}
    for asset_class, count in PICKS.items():
        allocation[asset_class], class_notes = _build_class(
            picks.get(asset_class), universe["assets"][asset_class], count, weights[asset_class])
        if class_notes:
            notes[asset_class] = class_notes

    return {
        "prompt": prompt,
        "macro_risk": {
            "score": macro_score,
            "label": macro["label"],
            "guidance": macro["guidance"],
            "as_of": macro["as_of"],
        },
        "risk_scale": {
            "macro": macro_score,
            "applied": applied,
            "label": label,
            "guidance": guidance,
            "adjustment_reason": answer.get("risk_adjustment_reason") or "",
        },
        "class_weights_pct": {k: round(v * 100, 2) for k, v in weights.items()},
        "allocation": allocation,
        "reasoning": answer.get("reasoning") or "",
        "notes": notes,
        "model": MODEL_NAME,
    }


if __name__ == "__main__":
    import sys
    print(json.dumps(run_agent(" ".join(sys.argv[1:]) or "Build me a portfolio."), indent=2))
