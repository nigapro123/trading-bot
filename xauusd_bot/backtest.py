"""Event-driven backtester for the top-down multi-timeframe scalping strategy.

It reuses the SAME `strategy.entry_from_values` / `risk` functions the live bot
uses, so results reflect the logic you actually trade. The higher-timeframe
trend and support/resistance are derived by RESAMPLING the execution-timeframe
data up to `cfg.htf_timeframe`, then aligned back to each execution bar without
look-ahead (each higher-TF bar's context is only used after that bar closes).

How a trade is simulated (no look-ahead):
  1. On bar t, the signal is computed from data up to bar t-1 (the last CLOSED
     execution bar) plus the most recent CLOSED higher-TF context.
  2. If a slot is free (< max_open_positions), entry fills at bar t's OPEN,
     adjusted by half the spread (adverse).
  3. SL/TP come from risk.stop_and_target; a support/resistance "room" check can
     veto the entry.
  4. On each later bar, if low/high touches SL/TP the position closes there
     (adverse half-spread + commission). If both could be hit in one bar, the
     STOP is assumed first (conservative).

Usage:
    python -m xauusd_bot.backtest --csv gold_m5.csv
    python -m xauusd_bot.backtest --synthetic
    python -m xauusd_bot.backtest --csv gold.csv --plot equity.png
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import data as data_mod
from . import metrics as metrics_mod
from . import risk, strategy
from .config import Config


@dataclass
class Instrument:
    """Contract specs for the traded symbol (gold defaults)."""
    name: str = "XAUUSD"
    trade_contract_size: float = 100.0   # ounces per 1.0 lot
    volume_step: float = 0.01
    volume_min: float = 0.01
    volume_max: float = 100.0
    point: float = 0.01
    digits: int = 2


@dataclass
class Costs:
    spread_points: int = 20        # full spread, in points (0.01 each) -> $0.20
    commission_per_lot: float = 0.0  # round-turn commission per lot, account ccy


# Map our timeframe codes to pandas resample frequencies.
_PANDAS_FREQ = {
    "M1": "1min", "M5": "5min", "M15": "15min", "M30": "30min",
    "H1": "1h", "H4": "4h", "D1": "1D",
}


def _htf_context(df: pd.DataFrame, cfg) -> pd.DataFrame:
    """Resample execution data to the higher TF; return per-execution-bar context.

    Returns a DataFrame aligned 1:1 to `df` rows with columns:
        htf_dir ('up'/'down'/None), support, resistance
    Each value reflects only higher-TF bars that had CLOSED by that exec bar.
    """
    freq = _PANDAS_FREQ.get(cfg.htf_timeframe)
    if freq is None:
        raise ValueError(f"Unknown htf_timeframe {cfg.htf_timeframe!r}")

    s = df.set_index("time")
    htf = pd.DataFrame({
        "high": s["high"].resample(freq).max(),
        "low": s["low"].resample(freq).min(),
        "close": s["close"].resample(freq).last(),
    }).dropna()

    ef = strategy.ema(htf["close"], cfg.ema_fast)
    es = strategy.ema(htf["close"], cfg.ema_slow)
    direction = np.where(ef > es, "up", np.where(ef < es, "down", None))
    htf_adx = strategy.adx(htf, cfg.adx_period)
    if getattr(cfg, "use_sr_filter", False):
        resistance = htf["high"].rolling(cfg.sr_lookback, min_periods=2).max()
        support = htf["low"].rolling(cfg.sr_lookback, min_periods=2).min()
    else:
        resistance = pd.Series(np.nan, index=htf.index)
        support = pd.Series(np.nan, index=htf.index)

    ctx = pd.DataFrame(
        {"htf_dir": direction, "support": support.values,
         "resistance": resistance.values, "htf_adx": htf_adx.values},
        index=htf.index,
    ).shift(1)   # a higher-TF bar's context is only usable AFTER it closes

    ctx = ctx.reset_index().rename(columns={"index": "time", "time": "time"})
    merged = pd.merge_asof(
        df[["time"]].sort_values("time"), ctx.sort_values("time"),
        on="time", direction="backward",
    )
    return merged


def _close_pnl(side: str, entry_fill: float, exit_fill: float,
               lots: float, instrument: Instrument, costs: Costs) -> float:
    direction = 1.0 if side == "buy" else -1.0
    gross = (exit_fill - entry_fill) * direction * instrument.trade_contract_size * lots
    return gross - costs.commission_per_lot * lots


def run_backtest(df: pd.DataFrame, cfg: Config, instrument: Instrument,
                 costs: Costs, starting_balance: float = 10_000.0) -> dict:
    warmup = max(cfg.ema_slow, cfg.rsi_period, cfg.atr_period, cfg.atr_avg_period) + 3
    if len(df) <= warmup + 5:
        raise ValueError(f"Need more than {warmup + 5} bars; got {len(df)}.")

    d = strategy.compute_indicators(df, cfg)
    ef = d["ema_fast"].to_numpy()
    es = d["ema_slow"].to_numpy()
    rsi_arr = d["rsi"].to_numpy()
    atr_arr = d["atr"].to_numpy()
    atr_avg_arr = d["atr_avg"].to_numpy()
    vwap_arr = d["vwap"].to_numpy()
    closes = df["close"].to_numpy()
    opens = df["open"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    times = df["time"].to_numpy()

    ctx = _htf_context(df, cfg)
    htf_dir = ctx["htf_dir"].to_numpy()
    support_arr = ctx["support"].to_numpy()
    resistance_arr = ctx["resistance"].to_numpy()
    htf_adx = ctx["htf_adx"].to_numpy()

    adaptive = getattr(cfg, "adaptive_regime", False)

    def regime_is_meanrev(j):
        """Per-bar regime: True=mean-reversion (range), False=trend-following."""
        if not adaptive:
            return getattr(cfg, "mean_reversion", False)
        a = htf_adx[j]
        if a != a:                      # NaN warmup -> default trend
            return False
        return a < cfg.trend_strength_adx

    half_spread = costs.spread_points * instrument.point / 2.0
    balance = starting_balance
    # 70% equity floor (realized-balance approximation): stop opening below this.
    equity_floor = starting_balance * (cfg.equity_floor_frac + cfg.equity_floor_buffer)
    positions: list[dict] = []     # up to cfg.max_open_positions concurrently
    trades = []

    def try_exit(pos, t_idx):
        hi, lo = highs[t_idx], lows[t_idx]
        tp = pos["tp"]   # None in mean-reversion mode (no fixed take-profit)
        if pos["side"] == "buy":
            if lo <= pos["sl"]:
                return pos["sl"], "sl"
            if tp is not None and hi >= tp:
                return tp, "tp"
        else:
            if hi >= pos["sl"]:
                return pos["sl"], "sl"
            if tp is not None and lo <= tp:
                return tp, "tp"
        return None, None

    def record(pos, exit_fill, reason, t_idx):
        nonlocal balance
        pnl = _close_pnl(pos["side"], pos["entry_fill"], exit_fill, pos["lots"],
                         instrument, costs)
        balance += pnl
        trades.append({
            "time": times[t_idx], "side": pos["side"], "entry": pos["entry_fill"],
            "exit": exit_fill, "lots": pos["lots"], "reason": reason, "pnl": pnl,
            "r": pnl / pos["risk_amount"] if pos["risk_amount"] else float("nan"),
            "equity_after": balance,
        })

    for t in range(warmup, len(df)):
        # 1) Manage every open position against this bar's range.
        survivors = []
        for pos in positions:
            # RSI-zone exit: mean-reversion trades only, and only if enabled.
            if (getattr(cfg, "meanrev_rsi_exit", False) and pos["mode"] == "meanrev"
                    and strategy.meanrev_exit(pos["side"], rsi_arr[t - 1], cfg)):
                direction = 1.0 if pos["side"] == "buy" else -1.0
                record(pos, opens[t] - direction * half_spread, "rsi", t)
                continue
            level, reason = try_exit(pos, t)
            if level is not None:
                direction = 1.0 if pos["side"] == "buy" else -1.0
                record(pos, level - direction * half_spread, reason, t)
            else:
                survivors.append(pos)
        positions = survivors

        # 2) Stop opening new trades once the equity floor is breached.
        if balance <= equity_floor:
            continue

        # 3) If a slot is free, evaluate the signal as of the last CLOSED bar.
        if len(positions) < cfg.max_open_positions:
            j = t - 1
            mr = regime_is_meanrev(j)          # per-bar regime (ADX-driven)
            if mr:
                signal = strategy.meanrev_entry(
                    rsi_arr[j - 1], rsi_arr[j], closes[j], vwap_arr[j], cfg
                )
            else:
                signal = strategy.entry_from_values(
                    ef[j - 1], es[j - 1], ef[j], es[j],
                    rsi_arr[j - 1], rsi_arr[j], closes[j], vwap_arr[j], htf_dir[j], cfg
                )
            atr_value = atr_arr[j]
            if signal and atr_value == atr_value:  # not NaN
                direction = 1.0 if signal == "buy" else -1.0
                entry_fill = opens[t] + direction * half_spread
                rr = strategy.effective_reward_risk(atr_value, atr_avg_arr[j], cfg)
                sl, tp, stop_dist = risk.stop_and_target(
                    signal, entry_fill, atr_value, cfg.atr_sl_mult, rr
                )
                if mr:
                    room = True        # keep the ATR tp; RSI-zone exit may close it sooner
                else:
                    tp_distance = rr * stop_dist
                    sup = support_arr[j]
                    res = resistance_arr[j]
                    room = strategy.has_room(
                        signal, entry_fill, tp_distance,
                        None if sup != sup else sup,        # NaN -> None (no level)
                        None if res != res else res,
                    )
                lots = risk.lot_size_by_balance(balance, cfg, instrument)
                if room and lots > 0:
                    risk_amount = stop_dist * instrument.trade_contract_size * lots
                    pos = {"side": signal, "entry_fill": entry_fill, "sl": sl,
                           "tp": tp, "lots": lots, "risk_amount": risk_amount,
                           "mode": "meanrev" if mr else "trend"}
                    # A freshly opened trade can also be stopped within bar t.
                    level, reason = try_exit(pos, t)
                    if level is not None:
                        record(pos, level - direction * half_spread, reason, t)
                    else:
                        positions.append(pos)

    trades_df = pd.DataFrame(trades)
    summary = metrics_mod.compute_metrics(trades_df, starting_balance)
    return {"metrics": summary, "trades": trades_df, "starting_balance": starting_balance}


def _maybe_plot(trades_df: pd.DataFrame, starting_balance: float, path: str) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return False
    eq = pd.concat([pd.Series([starting_balance]), trades_df["equity_after"]],
                   ignore_index=True)
    plt.figure(figsize=(10, 4))
    plt.plot(eq.values)
    plt.title("Equity curve")
    plt.xlabel("Trade #")
    plt.ylabel("Balance")
    plt.tight_layout()
    plt.savefig(path, dpi=120)
    return True


def main() -> None:
    p = argparse.ArgumentParser(description="Backtest the XAUUSD strategy")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", help="Path to an OHLC CSV (time,open,high,low,close)")
    src.add_argument("--synthetic", action="store_true", help="Use generated demo data")
    p.add_argument("--balance", type=float, default=10_000.0)
    p.add_argument("--spread-points", type=int, default=20)
    p.add_argument("--commission", type=float, default=0.0, help="Round-turn per lot")
    p.add_argument("--trades-csv", help="Optional path to dump the trade list")
    p.add_argument("--plot", help="Optional path to save the equity-curve PNG")
    args = p.parse_args()

    df = data_mod.synthetic() if args.synthetic else data_mod.load_csv(args.csv)
    cfg = Config()
    costs = Costs(spread_points=args.spread_points, commission_per_lot=args.commission)

    result = run_backtest(df, cfg, Instrument(), costs, starting_balance=args.balance)
    print(f"\nBacktest over {len(df)} bars "
          f"({df['time'].iloc[0]} -> {df['time'].iloc[-1]})\n")
    print(metrics_mod.format_metrics(result["metrics"]))

    if args.trades_csv and len(result["trades"]):
        result["trades"].to_csv(args.trades_csv, index=False)
        print(f"\nTrade list written to {args.trades_csv}")
    if args.plot and len(result["trades"]):
        ok = _maybe_plot(result["trades"], args.balance, args.plot)
        print(f"\nEquity curve saved to {args.plot}" if ok
              else "\n(matplotlib not installed; skipped plot)")


if __name__ == "__main__":
    main()
