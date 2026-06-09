"""Performance metrics shared by the backtester and the live-trade analyzer.

Keeping this in one place means a demo/live run and a backtest are scored the
exact same way, so the numbers are directly comparable.

Expected trades DataFrame columns:
    pnl           realized profit/loss in account currency
    r             profit/loss expressed in R (pnl / risk_at_entry); may be NaN
    equity_after  account equity immediately after the trade closed
"""
from __future__ import annotations

import pandas as pd


def compute_metrics(trades: pd.DataFrame, starting_balance: float) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0}

    wins = trades[trades["pnl"] > 0]
    losses = trades[trades["pnl"] <= 0]
    gross_profit = float(wins["pnl"].sum())
    gross_loss = float(-losses["pnl"].sum())
    net = float(trades["pnl"].sum())

    # Equity curve including the starting point, for max drawdown.
    eq = pd.concat(
        [pd.Series([starting_balance]), trades["equity_after"]], ignore_index=True
    )
    peak = eq.cummax()
    drawdown = (eq - peak) / peak

    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": len(wins) / n * 100,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else float("inf"),
        "net_profit": net,
        "final_balance": starting_balance + net,
        "return_pct": net / starting_balance * 100,
        "avg_R": float(trades["r"].mean()),
        "expectancy_R": float(trades["r"].mean()),  # mean R per trade
        "max_drawdown_pct": float(drawdown.min() * 100),
    }


def format_metrics(m: dict) -> str:
    if m.get("trades", 0) == 0:
        return "No trades were taken."
    pf = m["profit_factor"]
    pf_str = "inf" if pf == float("inf") else f"{pf:.2f}"
    return (
        f"Trades:          {m['trades']}\n"
        f"Win rate:        {m['win_rate_pct']:.1f}%  ({m['wins']}W / {m['losses']}L)\n"
        f"Net profit:      {m['net_profit']:,.2f}  ({m['return_pct']:+.2f}%)\n"
        f"Final balance:   {m['final_balance']:,.2f}\n"
        f"Profit factor:   {pf_str}\n"
        f"Expectancy:      {m['expectancy_R']:+.3f} R per trade\n"
        f"Max drawdown:    {m['max_drawdown_pct']:.2f}%"
    )
