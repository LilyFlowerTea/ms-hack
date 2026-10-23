PROJECT WEBSITE LINK:
https://deployed-ms-hack.vercel.app/

# Example Project

Minimal Python samples that call the OpenAI Portal using the `openai` SDK:

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

### 5. Run the samples

Run the chat completion example:

```bash
python main.py
```

You should see the assistant's response printed to the terminal.

Run the embeddings example:

```bash
python embeddings.py
```

This sends three short phrases to the `text-embedding-3-large` deployment and prints:

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
