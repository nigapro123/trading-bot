"""Grid-search optimizer — the main tool for *correcting* the strategy.

It re-runs the backtest across combinations of the key parameters and ranks
them, so you can see which settings would have held up on your historical data.

IMPORTANT: good in-sample numbers do NOT guarantee future results. Treat the
optimizer as a way to find *robust* regions (many nearby settings all decent),
not a single magic combination. Always validate the winner on data it was not
tuned on (e.g. a later date range) and then forward-test on demo.

Usage:
    python -m xauusd_bot.optimize --csv gold_m15.csv
    python -m xauusd_bot.optimize --synthetic --min-trades 20 --top 15
"""
from __future__ import annotations

import argparse
import dataclasses
import itertools

import pandas as pd

from . import data as data_mod
from .backtest import Costs, Instrument, run_backtest
from .config import Config

# Edit these grids to search a different space.
# Scalping-oriented defaults: fast EMA pairs, tight ATR stops, ~1:1 targets.
GRID = {
    "ema_fast": [5, 9, 13],
    "ema_slow": [21, 34, 50],
    "atr_sl_mult": [1.0, 1.5, 2.0],
    "reward_risk": [1.0, 1.5],
}


def optimize(df: pd.DataFrame, base_cfg: Config, instrument: Instrument,
             costs: Costs, starting_balance: float, min_trades: int) -> pd.DataFrame:
    keys = list(GRID)
    rows = []
    for combo in itertools.product(*(GRID[k] for k in keys)):
        params = dict(zip(keys, combo))
        if params["ema_fast"] >= params["ema_slow"]:
            continue  # fast must be faster than slow
        cfg = dataclasses.replace(base_cfg, **params)
        try:
            res = run_backtest(df, cfg, instrument, costs, starting_balance)
        except ValueError:
            continue
        m = res["metrics"]
        if m.get("trades", 0) < min_trades:
            continue
        rows.append({
            **params,
            "trades": m["trades"],
            "win_rate_pct": round(m["win_rate_pct"], 1),
            "return_pct": round(m["return_pct"], 2),
            "profit_factor": round(m["profit_factor"], 3) if m["profit_factor"] != float("inf") else 999,
            "expectancy_R": round(m["expectancy_R"], 3),
            "max_drawdown_pct": round(m["max_drawdown_pct"], 2),
        })

    results = pd.DataFrame(rows)
    if results.empty:
        return results
    # Rank by profit factor, then expectancy, then shallower drawdown.
    return results.sort_values(
        ["profit_factor", "expectancy_R", "max_drawdown_pct"],
        ascending=[False, False, False],
    ).reset_index(drop=True)


def main() -> None:
    p = argparse.ArgumentParser(description="Optimize the XAUUSD strategy parameters")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv")
    src.add_argument("--synthetic", action="store_true")
    p.add_argument("--balance", type=float, default=10_000.0)
    p.add_argument("--spread-points", type=int, default=20)
    p.add_argument("--commission", type=float, default=0.0)
    p.add_argument("--min-trades", type=int, default=15,
                   help="Discard settings with fewer trades (too few = noise)")
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--out-csv", help="Optional path to save the full ranking")
    args = p.parse_args()

    df = data_mod.synthetic() if args.synthetic else data_mod.load_csv(args.csv)
    costs = Costs(spread_points=args.spread_points, commission_per_lot=args.commission)
    ranking = optimize(df, Config(), Instrument(), costs, args.balance, args.min_trades)

    if ranking.empty:
        print(f"No parameter set produced at least {args.min_trades} trades.")
        return
    pd.set_option("display.width", 120)
    print(f"\nTop {min(args.top, len(ranking))} of {len(ranking)} valid combinations:\n")
    print(ranking.head(args.top).to_string(index=False))
    if args.out_csv:
        ranking.to_csv(args.out_csv, index=False)
        print(f"\nFull ranking written to {args.out_csv}")


if __name__ == "__main__":
    main()
