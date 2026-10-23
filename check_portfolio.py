"""Run a portfolio backtest and summarize its results with Azure OpenAI."""

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ai_config import get_azure_client, get_chat_model
from backtest import Backtest
from tax import check_tax_efficiency


def _parse_portfolio(portfolio_json: Mapping[str, Any] | str | Path) -> Mapping[str, Any]:
    if isinstance(portfolio_json, Mapping):
        return portfolio_json

    if isinstance(portfolio_json, Path):
        with portfolio_json.open(encoding="utf-8") as portfolio_file:
            portfolio = json.load(portfolio_file)
    elif isinstance(portfolio_json, str):
        try:
            portfolio = json.loads(portfolio_json)
        except json.JSONDecodeError as error:
            raise ValueError("Portfolio input must be valid JSON text.") from error
    else:
        raise TypeError("Portfolio input must be a JSON object, JSON string, or file path.")

    if not isinstance(portfolio, Mapping):
        raise ValueError("Portfolio JSON must contain an object.")
    return portfolio


def check_portfolio(portfolio_json: Mapping[str, Any] | str | Path) -> str:
    """Backtest a JSON portfolio and return a concise natural-language summary."""
    portfolio = _parse_portfolio(portfolio_json)
    backtest = Backtest(portfolio)
    results = backtest.run()

    portfolio_summary = summarize_backtest(backtest.portfolio, results)
    tax_summary = check_tax_efficiency(backtest.portfolio, results)
    return (
        f"{portfolio_summary}\n\n"
        f"UK corporation-tax efficiency\n{tax_summary}"
    )


def summarize_backtest(
    portfolio: Mapping[str, Any],
    results: Mapping[str, Any],
) -> str:
    """Summarize metrics from an already-completed backtest."""
    summary_results = {key: value for key, value in results.items() if key != "equity_curve"}
    report = {
        "holdings": portfolio["holdings"],
        "backtest_results": summary_results,
    }

    client = get_azure_client()
    try:
        response = client.chat.completions.create(
            model=get_chat_model(),
            max_completion_tokens=1000,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Summarize the supplied portfolio backtest accurately and concisely. "
                        "Use only the supplied data, explain returns and risk metrics in plain "
                        "language, and state that historical performance does not predict future "
                        "results. Do not present this as financial or tax advice. At the end of "
                        "the summary, provide the JSON from backtest results in a code block."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(report, indent=2),
                },
            ],
        )

        if not response.choices or not response.choices[0].message.content:
            raise RuntimeError("The model returned no portfolio summary.")
        return response.choices[0].message.content
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backtest a JSON portfolio and summarize the results."
    )
    parser.add_argument("portfolio", type=Path, help="Path to a portfolio JSON file.")
    args = parser.parse_args()
    print(check_portfolio(args.portfolio))


if __name__ == "__main__":
    main()
