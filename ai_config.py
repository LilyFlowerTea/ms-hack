"""Shared Azure OpenAI configuration for the application and agents."""

import os
from pathlib import Path

from dotenv import load_dotenv
from openai import AzureOpenAI

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")


class AzureConfigurationError(RuntimeError):
    """Raised when required Azure OpenAI settings are missing."""


def get_azure_client() -> AzureOpenAI:
    api_key = os.environ.get("AZURE_OPENAI_API_KEY") or os.environ.get("api_key")
    if not api_key:
        raise AzureConfigurationError(
            "Set AZURE_OPENAI_API_KEY in the environment or .env file."
        )

    return AzureOpenAI(
        api_version=(
            os.environ.get("AZURE_OPENAI_API_VERSION")
            or os.environ.get("api_version")
            or "2024-12-01-preview"
        ),
        azure_endpoint=os.environ.get(
            "AZURE_OPENAI_ENDPOINT",
            "https://app-uoaihack2ix6ak.azurewebsites.net",
        ),
        api_key=api_key,
        timeout=45.0,
        max_retries=1,
    )


def get_chat_model() -> str:
    return os.environ.get("AZURE_OPENAI_MODEL", "gpt-6.1-sol")
