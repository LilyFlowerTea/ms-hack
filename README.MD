PROJECT WEBSITE LINK:
https://ms-hack-new.vercel.app/

# Imperial Capital Allocation

Web app for investment conversations, portfolio backtesting, and a qualified UK
corporation-tax review. It also includes standalone Azure OpenAI examples:

- [main.py](main.py) — chat completions example (`gpt-6.1-sol`)
- [embeddings.py](embeddings.py) — text embeddings example (`text-embedding-3-large`)

## Quickstart

### Prerequisites

- Python 3.9 or later

### 1. Clone the repository

```bash
git clone <your-repo-url>
cd example-hackathon-python
```

### 2. Create and activate a virtual environment

On Windows (PowerShell):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

On macOS/Linux:

```bash
python -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure credentials

Create a `.env` file from `.env.example` and set the Azure values:

- `AZURE_OPENAI_API_KEY` — your API key
- `AZURE_OPENAI_ENDPOINT` — the endpoint URL
- `AZURE_OPENAI_API_VERSION` — the API version
- `AZURE_OPENAI_MODEL` — the chat deployment name
- `AZURE_OPENAI_EMBEDDING_MODEL` — the embeddings deployment name

The main and tax assistants load their system prompts from
`prompts/main_system_prompt.txt` and `prompts/tax_system_prompt.txt` respectively.
Edit those files to update their behavior.

> **Tip:** For real projects, load these values from environment variables (e.g. via `os.environ` or `python-dotenv`) instead of hard-coding them.

### 5. Run the application

Start the FastAPI app from the repository root:

```powershell
uvicorn api.index:app --reload --port 1234
```

Open `http://127.0.0.1:1234`. The page provides investment and tax chats, a
portfolio-builder JSON handoff, and a portfolio backtest. The backtest uses
adjusted historical prices from Yahoo Finance, then returns performance metrics,
a line chart, a portfolio summary, and a UK corporation-tax assessment.

Submit a portfolio in this format:

```bash
{
  "holdings": {"AAPL": 0.6, "MSFT": 0.4},
  "start": "2020-01-01",
  "end": "2024-01-01",
  "initial_cash": 10000
}
```

Weights must be positive and sum to 1. The `end` date is exclusive. The backtest
and tax review are also exposed at `POST /api/backtest`. The UK tax assessment
is informational: portfolio prices and weights alone cannot establish taxable
profits or tax-optimality.

The app reads Azure settings from `.env` using the `AZURE_OPENAI_*` keys in
`.env.example`; legacy `api_key` and `api_version` values are also supported.

### 6. Run the standalone examples

Run the chat completion example:

```bash
python main.py
```

Run the embeddings example:

```bash
python embeddings.py
```

This sends three short phrases to the embedding deployment and prints:

- the full JSON response from the embeddings API
- a one-line summary per input showing the embedding vector length and the first/last two values
- token usage information

Use the endpoint, key, API version, and deployment names provided by your team.

## Troubleshooting

- **`AuthenticationError`** — verify your `subscription_key` and `endpoint` are correct.
- **`DeploymentNotFound`** — make sure the `deployment` name matches what model you want to use in the portal.
- **SSL or network errors** — check that your machine can reach the endpoint and it is not being blocked by any VPN or proxies.

## Macro risk score API

`GET /api/risk` returns a score from 0 to 1, calculated from the latest FRED (St. Louis Fed) data:

- **0**: very conservative (bonds, money market)
- **1**: very aggressive (stocks, equity ETFs)

Run locally with `uvicorn api.index:app --reload`, then open http://127.0.0.1:8000/api/risk.

Query params:

- `lookback_years` (3–30, default 10): history window each indicator is ranked against.
- `trend_months` (1–24, default 6): period over which recent change is measured.

| Indicator | FRED series | Weight | Higher means |
|---|---|---|---|
| Yield curve 10Y-2Y | T10Y2Y | 0.15 | risk-on |
| Baa corporate spread | BAA10Y | 0.20 | risk-off |
| VIX | VIXCLS | 0.15 | risk-off |
| Chicago Fed financial conditions | NFCI | 0.15 | risk-off |
| Unemployment momentum (Sahm gap) | UNRATE | 0.15 | risk-off |
| Real fed funds rate | DFF − CPI YoY | 0.10 | risk-off |
| Industrial production YoY | INDPRO | 0.10 | risk-on |

Each indicator gets a **level** score and a **trend** score. Level is the percentile of the latest value within the lookback window. Trend is the percentile of its latest N-month change. The two are blended 60/40 and flipped where needed, so 1 always means risk-on. The final score is the weighted average. Results are cached for 6 hours. `FRED_API_KEY` is optional; without it the API uses FRED's public CSV download.

## Assets API

`GET /api/assets?days=30` returns the largest US assets in one call, with price, news and analyst ratings over the last `days` days.

| Asset class | Count | Ranked by |
|---|---|---|
| `stocks` | 100 | Market cap, NASDAQ/NYSE/NYSE American, one share class per company |
| `etfs` | 50 | Net assets, excluding bond and money market ETFs (equity, gold, etc.) |
| `bonds` | 50 | Net assets, bond ETFs |
| `money_market` | 25 | Net assets, ultrashort bond ETFs (T-bill and money-market-style, such as SGOV, BIL and JPST) |

Query params:

- `days` (1–365, default 30): the time horizon, counted back from today.
- `news_limit` (0–50, default 5): the maximum number of news articles per stock.

Each asset includes:

- **Basics:** rank, symbol, name, exchange, `size_usd` (market cap or net assets), dividend yield, plus P/E for stocks or expense ratio for funds.
- **`price`:** current price, daily change, and the start price, % change, high, low and annualised volatility over the horizon.
- **`ratings`** (stocks only): Yahoo consensus (1 = strong buy, 5 = strong sell), counts of upgrades, downgrades and initiations, average price target, and every analyst action within the horizon.
- **`news`** (stocks only): number of articles in the horizon and the newest `news_limit` articles. `covers_from` is the oldest article Finnhub returned. Finnhub sends at most about 250 articles, so for heavily covered stocks this can be later than the start of the horizon.

Data sources: Yahoo Finance (`yfinance`) for rankings, prices and analyst actions, and Finnhub for news. Set `FINNHUB_API_KEY` in `.env` (you can get a free key at https://finnhub.io/dashboard).

Performance: the server starts loading data on startup. Finnhub's free plan allows 60 calls per minute and news needs one call per stock, so the first response takes about 2 minutes. After that, any horizon is answered from the cache. News refreshes hourly in the background, and prices refresh every 15 minutes.

Money market mutual funds (VMFXX, SPAXX and similar) are excluded because Yahoo's fund sizes for them are wrong. For example, it reports Fidelity's funds as $1M.

## Portfolio agent API

`POST /api/agent` with `{"prompt": "I'm 30, saving for retirement, moderate risk"}` runs an LLM agent (`gpt-6.1-sol`). It loads the macro risk score and assets APIs, passes them to the model with the prompt, and returns:

- **`macro_risk`**: the FRED score from `/api/risk`.
- **`risk_scale`**: `macro` and `applied`. The model may move the scale by up to ±0.2 from the macro score to fit the prompt (age, horizon, goals), and gives `adjustment_reason`.
- **`class_weights_pct`**: derived from the applied scale `r`. Stocks get `0.7r`, ETFs `0.3r`, and fixed income `1 − r`. Money market's share of fixed income grows as `r` falls, from 20% at `r = 1` to 70% at `r = 0`.
- **`allocation`**: 20 stocks, 10 ETFs, 10 bonds and 5 money market funds, all taken from `/api/assets`. Each holding has `weight_pct` (of the whole portfolio), `weight_in_class_pct`, price data and a one-line rationale. Weights add up to 100%.
- **`reasoning`**: the agent's explanation.
- **`notes`**: any corrections the server made, such as dropping symbols outside the asset class or adding the largest assets when the model picked too few.

Uses the same `AZURE_OPENAI_*` settings in `.env` as the chat endpoints. The first call waits for the assets cache (about 2 minutes after startup). Try it from the CLI with `python -m api.agent "your prompt"`.
