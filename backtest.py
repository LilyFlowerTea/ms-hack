"""Backtest a buy-and-hold portfolio using historical Yahoo Finance data.

JSON format: 

{
  "holdings": {"AAPL": 0.6, "MSFT": 0.4},
  "start": "2020-01-01",
  "end": "2024-01-01",
  "initial_cash": 10000
}

"""

import argparse
from io import BytesIO
import json
import math
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd
import yfinance as yf


class Backtest:
    def __init__(
        self,
        portfolio: Mapping[str, Any],
        historical_data: pd.DataFrame | None = None,
    ) -> None:
        """Initialise from portfolio settings and optional price data."""
        self.portfolio = self._validate_portfolio(portfolio)
        self.historical_data = historical_data

    def run(self) -> dict[str, Any]:
        """Run the backtest and return performance metrics and daily values."""
        prices = self._get_prices()
        tickers = list(self.portfolio["holdings"])
        prices = prices.reindex(columns=tickers)

        if prices.isna().all().any():
            missing = prices.columns[prices.isna().all()].tolist()
            raise ValueError(f"No price data returned for ticker(s): {', '.join(missing)}")

        prices = prices.apply(pd.to_numeric, errors="coerce").sort_index()
        prices.index = pd.to_datetime(prices.index)
        prices = prices.loc[
            (prices.index >= pd.Timestamp(self.portfolio["start"]))
            & (prices.index < pd.Timestamp(self.portfolio["end"]))
        ]
        prices = prices.dropna(how="all").ffill().dropna()
        if prices.empty:
            raise ValueError("No dates contain prices for every portfolio holding.")
        if (prices <= 0).any().any():
            raise ValueError("Price data must contain only positive prices.")

        weights = pd.Series(self.portfolio["holdings"], dtype=float)
        shares = self.portfolio["initial_cash"] * weights / prices.iloc[0]
        equity = prices.mul(shares, axis="columns").sum(axis="columns")
        returns = equity.pct_change().dropna()

        elapsed_days = (equity.index[-1] - equity.index[0]).days
        total_return = equity.iloc[-1] / self.portfolio["initial_cash"] - 1
        annualized_return = (
            (equity.iloc[-1] / self.portfolio["initial_cash"]) ** (365.25 / elapsed_days) - 1
            if elapsed_days > 0
            else total_return
        )
        daily_volatility = returns.std()
        sharpe_ratio = (
            returns.mean() / daily_volatility * math.sqrt(252)
            if pd.notna(daily_volatility) and daily_volatility > 0
            else 0.0
        )
        drawdown = equity / equity.cummax() - 1

        return {
            "start_date": equity.index[0].strftime("%Y-%m-%d"),
            "end_date": equity.index[-1].strftime("%Y-%m-%d"),
            "initial_value": float(self.portfolio["initial_cash"]),
            "final_value": float(equity.iloc[-1]),
            "total_return_pct": float(total_return * 100),
            "annualized_return_pct": float(annualized_return * 100),
            "annualized_volatility_pct": (
                float(daily_volatility * math.sqrt(252) * 100)
                if pd.notna(daily_volatility)
                else 0.0
            ),
            "sharpe_ratio": float(sharpe_ratio),
            "max_drawdown_pct": float(drawdown.min() * 100),
            "equity_curve": [
                {"date": timestamp.strftime("%Y-%m-%d"), "value": float(value)}
                for timestamp, value in equity.items()
            ],
        }

    def plot(self, results: Mapping[str, Any]) -> bytes:
        """Return the portfolio's daily value line chart as PNG bytes."""
        equity_curve = results["equity_curve"]
        dates = pd.to_datetime([point["date"] for point in equity_curve])
        values = [point["value"] for point in equity_curve]

        figure, axis = plt.subplots(figsize=(10, 5))
        axis.plot(dates, values)
        axis.set_title("Portfolio Value Over Time")
        axis.set_xlabel("Date")
        axis.set_ylabel("Portfolio Value")
        axis.grid(True, alpha=0.3)
        figure.autofmt_xdate()
        figure.tight_layout()

        image = BytesIO()
        figure.savefig(image, format="png")
        plt.close(figure)
        return image.getvalue()

    def _get_prices(self) -> pd.DataFrame:
        if self.historical_data is not None:
            return self._extract_close_prices(self.historical_data)

        data = yf.download(
            tickers=list(self.portfolio["holdings"]),
            start=self.portfolio["start"],
            end=self.portfolio["end"],
            auto_adjust=True,
            progress=False,
        )
        if data.empty:
            raise ValueError("Yahoo Finance returned no historical data for the requested period.")
        return self._extract_close_prices(data)

    def _extract_close_prices(self, data: pd.DataFrame) -> pd.DataFrame:
        tickers = list(self.portfolio["holdings"])
        if isinstance(data.columns, pd.MultiIndex):
            for level in range(data.columns.nlevels):
                if "Close" in data.columns.get_level_values(level):
                    close = data.xs("Close", axis="columns", level=level)
                    break
            else:
                raise ValueError("Historical data does not contain adjusted close prices.")
        elif "Close" in data.columns:
            close = data["Close"]
        elif set(tickers).issubset(data.columns):
            close = data[tickers]
        else:
            raise ValueError("Historical data must contain adjusted close prices for each holding.")

        if isinstance(close, pd.Series):
            close = close.to_frame(name=tickers[0])
        return close

    @staticmethod
    def _validate_portfolio(portfolio: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(portfolio, Mapping):
            raise ValueError("Portfolio JSON must contain an object.")

        holdings = portfolio.get("holdings")
        if not isinstance(holdings, Mapping) or not holdings:
            raise ValueError("'holdings' must be a non-empty object of ticker weights.")

        validated_holdings: dict[str, float] = {}
        for ticker, weight in holdings.items():
            if not isinstance(ticker, str) or not ticker.strip():
                raise ValueError("Holding tickers must be non-empty strings.")
            if isinstance(weight, bool) or not isinstance(weight, (int, float)):
                raise ValueError(f"Weight for {ticker} must be a number.")
            if not math.isfinite(weight) or weight <= 0:
                raise ValueError(f"Weight for {ticker} must be a positive finite number.")
            validated_holdings[ticker.strip().upper()] = float(weight)

        if not math.isclose(sum(validated_holdings.values()), 1.0, rel_tol=1e-5, abs_tol=1e-5):
            raise ValueError("Holding weights must sum to 1.")

        start = portfolio.get("start")
        end = portfolio.get("end")
        if not isinstance(start, str) or not isinstance(end, str):
            raise ValueError("'start' and 'end' must be date strings in YYYY-MM-DD format.")
        try:
            start_date = date.fromisoformat(start)
            end_date = date.fromisoformat(end)
        except ValueError as error:
            raise ValueError("'start' and 'end' must be valid YYYY-MM-DD dates.") from error
        if start_date >= end_date:
            raise ValueError("'end' must be after 'start'.")

        initial_cash = portfolio.get("initial_cash")
        if (
            isinstance(initial_cash, bool)
            or not isinstance(initial_cash, (int, float))
            or not math.isfinite(initial_cash)
            or initial_cash <= 0
        ):
            raise ValueError("'initial_cash' must be a positive finite number.")

        return {
            "holdings": validated_holdings,
            "start": start,
            "end": end,
            "initial_cash": float(initial_cash),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest a JSON portfolio with Yahoo Finance data.")
    parser.add_argument("portfolio", type=Path, help="Path to a portfolio JSON file.")
    args = parser.parse_args()

    with args.portfolio.open(encoding="utf-8") as portfolio_file:
        portfolio = json.load(portfolio_file)

    backtest = Backtest(portfolio)
    results = backtest.run()
    print(json.dumps(results, indent=2))
    chart_path = args.portfolio.with_suffix(".png")
    chart_path.write_bytes(backtest.plot(results))
    print(f"Chart saved to {chart_path}")


if __name__ == "__main__":
    main()
