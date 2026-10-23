import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from openai import AzureOpenAI, OpenAIError
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "templates"
STATIC_DIR = PROJECT_ROOT / "static"
PROMPTS_DIR = PROJECT_ROOT / "prompts"
MAIN_SYSTEM_PROMPT = (PROMPTS_DIR / "main_system_prompt.txt").read_text(encoding="utf-8").strip()
TAX_SYSTEM_PROMPT = (PROMPTS_DIR / "tax_system_prompt.txt").read_text(encoding="utf-8").strip()
load_dotenv(PROJECT_ROOT / ".env")

app = FastAPI(title="Imperial Capital Allocation")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

AZURE_OPENAI_ENDPOINT = os.environ.get(
    "AZURE_OPENAI_ENDPOINT",
    "https://app-uoaihack2ix6ak.azurewebsites.net",
)
AZURE_OPENAI_API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
AZURE_OPENAI_MODEL = os.environ.get("AZURE_OPENAI_MODEL", "gpt-6.1-sol")


class ConversationTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=12000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=12000)
    history: list[ConversationTurn] = Field(default_factory=list, max_length=30)
    time_horizon: int = Field(ge=1, le=10)


class TaxRequest(BaseModel):
    message: str = Field(min_length=1, max_length=12000)
    history: list[ConversationTurn] = Field(default_factory=list, max_length=30)
    time_horizon: int = Field(ge=1, le=10)


def get_azure_client() -> AzureOpenAI:
    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="The Azure OpenAI API key is not configured. Add AZURE_OPENAI_API_KEY to your environment.",
        )
    return AzureOpenAI(
        api_version=AZURE_OPENAI_API_VERSION,
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_key=api_key,
        timeout=45.0,
        max_retries=1,
    )


def get_reply(system_prompt: str, user_messages: list[dict[str, str]]) -> str:
    client = get_azure_client()
    try:
        response = client.chat.completions.create(
            model=AZURE_OPENAI_MODEL,
            messages=[{"role": "system", "content": system_prompt}, *user_messages],
            max_completion_tokens=1200,
        )
        reply = response.choices[0].message.content
        if not reply:
            raise HTTPException(status_code=502, detail="The assistant returned an empty reply.")
        return reply
    except OpenAIError as exc:
        status = getattr(exc, "status_code", None)
        code = getattr(exc, "code", None)
        reason = f"Azure OpenAI request failed (HTTP {status}"
        if code:
            reason += f", {code}"
        reason += "). Check the endpoint, API version, deployment name, and credentials."
        raise HTTPException(
            status_code=502,
            detail=reason,
        ) from exc
    finally:
        client.close()


# Render the webpage from the templates directory.
@app.get("/", include_in_schema=False)
def homepage(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={},
    )


@app.post("/api/chat")
def chat(request: ChatRequest) -> dict[str, str]:
    system_prompt = (
        f"{MAIN_SYSTEM_PROMPT}\n\n"
        "Trusted session information:\n"
        f"Time horizon selected on the page: {request.time_horizon} years."
    )
    context_message = (
        f"Startup's message: {request.message}"
    )
    messages = [turn.model_dump() for turn in request.history]
    messages.append({"role": "user", "content": context_message})
    return {"reply": get_reply(system_prompt, messages)}


@app.post("/api/tax")
def tax_assistant(request: TaxRequest) -> dict[str, str]:
    tax_message = (
        f"Investment horizon selected on the page: {request.time_horizon} years.\n\n"
        f"Founder’s message: {request.message}"
    )
    messages = [turn.model_dump() for turn in request.history]
    messages.append({"role": "user", "content": tax_message})
    return {
        "reply": get_reply(
            TAX_SYSTEM_PROMPT,
            messages,
        )
    }

# uvicorn api.index:app --reload --port 1234
