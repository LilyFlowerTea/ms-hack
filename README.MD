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

Open [main.py](main.py) and update the following values with your own:

- `api_key` — your API Key
- `model_name` — the name of the model

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

Update `api_key`, and `model_name` your team's credentials in the portal.

## Troubleshooting

- **`AuthenticationError`** — verify your `subscription_key` and `endpoint` are correct.
- **`DeploymentNotFound`** — make sure the `deployment` name matches what model you want to use in the portal.
- **SSL or network errors** — check that your machine can reach the endpoint and it is not being blocked by any VPN or proxies.
