"""Score the live/demo trade journal with the SAME metrics as the backtest.

Run this after letting the bot trade on a demo account for a while:

    python -m xauusd_bot.analyze_live --journal live_trades.csv --balance 10000

Compare the output against your backtest over the same period. Big gaps usually
mean real-world friction the backtest under-modelled (spread, slippage,
requotes) or that the market regime changed — both are signals to re-run the
optimizer and adjust before trusting the strategy further.
"""
from __future__ import annotations

import argparse

import pandas as pd

from . import metrics as metrics_mod


def main() -> None:
    p = argparse.ArgumentParser(description="Analyze the live trade journal")
    p.add_argument("--journal", default="live_trades.csv")
    p.add_argument("--balance", type=float, default=10_000.0,
                   help="Starting balance used for return / drawdown figures")
    args = p.parse_args()

    df = pd.read_csv(args.journal)
    for col in ("pnl", "r", "equity_after"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["pnl", "equity_after"]).reset_index(drop=True)

    m = metrics_mod.compute_metrics(df, args.balance)
    print(f"\nLive journal: {args.journal}\n")
    print(metrics_mod.format_metrics(m))


if __name__ == "__main__":
    main()
