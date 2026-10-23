"""Assess a portfolio's UK corporation-tax efficiency from available data."""

import json
from collections.abc import Mapping
from typing import Any

from ai_config import get_azure_client, get_chat_model
government_sources = (
    (
        "Corporation Tax rates, expenses and reliefs",
        "https://www.gov.uk/corporation-tax-rates",
    ),
    (
        "Corporation Tax when you sell company assets",
        "https://www.gov.uk/tax-when-your-company-sells-assets",
    ),
)


def check_tax_efficiency(
    portfolio: Mapping[str, Any],
    backtest_results: Mapping[str, Any],
) -> str:
    """Return a qualified UK corporation-tax efficiency assessment."""
    results = {
        key: value
        for key, value in backtest_results.items()
        if key != "equity_curve"
    }
    input_data = {
        "tax_context": "UK company subject to corporation-tax rules",
        "portfolio": {
            "holdings": portfolio["holdings"],
            "start": portfolio["start"],
            "end": portfolio["end"],
            "initial_cash": portfolio["initial_cash"],
        },
        "backtest_results": results,
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
                        "Assess the supplied investment portfolio's UK corporation-tax "
                        "efficiency using only the supplied data and the included official "
                        "GOV.UK references. This is preliminary educational information, not tax "
                        "advice or a calculation of tax due. The input only includes holdings, "
                        "dates, initial capital, and pre-tax backtest performance, so it cannot "
                        "establish taxable profits or determine whether the portfolio is tax "
                        "optimal. Begin with the exact verdict 'Tax-optimality: UNDETERMINED "
                        "(insufficient tax and accounting data).' Do not claim the portfolio is "
                        "tax-optimal or tax-inefficient without evidence. Explain that realized "
                        "disposals, acquisition costs, income and distributions, expenses, "
                        "accounting period, company circumstances, losses, and applicable "
                        "reliefs are missing. Explain in general terms that UK company tax on "
                        "profits and gains depends on accounting-period rules, profit levels, "
                        "and eligible reliefs, referring to the provided GOV.UK pages. Do not "
                        "infer tax treatment, company status, fund domicile, dividend character, "
                        "or rates from ticker symbols. State what additional records a qualified "
                        "adviser would need. Do not assert a specific tax liability or recommend "
                        "transactions."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            **input_data,
                            "official_uk_government_sources": [
                                {"title": title, "url": url}
                                for title, url in government_sources
                            ],
                        },
                        indent=2,
                    ),
                },
            ],
        )

        if not response.choices or not response.choices[0].message.content:
            raise RuntimeError("The model returned no UK corporation-tax assessment.")
        model_assessment = response.choices[0].message.content
    finally:
        client.close()

    verdict = (
        "Tax-optimality: UNDETERMINED "
        "(insufficient tax and accounting data; the portfolio is not certified as tax-optimal)."
    )
    source_list = "\n".join(
        f"- [{title}]({url})" for title, url in government_sources
    )
    return (
        f"{verdict}\n\n"
        f"{model_assessment}\n\n"
        f"Official UK government context (GOV.UK):\n{source_list}"
    )