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
