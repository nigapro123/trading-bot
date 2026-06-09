"""Historical data utilities for backtesting.

Three ways to get bars:
  * load_csv()          - read an OHLC CSV you already have
  * synthetic()         - generate fake bars for quick smoke tests
  * export_mt5_history()- pull real history from your broker into a CSV

The backtester only needs columns: time, open, high, low, close.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Common column name variants mapped to our canonical names.
_ALIASES = {
    "time": "time", "date": "time", "datetime": "time", "timestamp": "time",
    "open": "open", "o": "open",
    "high": "high", "h": "high",
    "low": "low", "l": "low",
    "close": "close", "c": "close", "price": "close",
}


def load_csv(path: str) -> pd.DataFrame:
    """Load an OHLC CSV with flexible column names. Returns oldest-first bars."""
    df = pd.read_csv(path)
    df.columns = [str(c).strip().lower() for c in df.columns]
    renamed = {c: _ALIASES[c] for c in df.columns if c in _ALIASES}
    df = df.rename(columns=renamed)

    missing = {"open", "high", "low", "close"} - set(df.columns)
    if missing:
        raise ValueError(f"CSV is missing required column(s): {sorted(missing)}")

    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"], errors="coerce")
        df = df.sort_values("time").reset_index(drop=True)
    else:
        df["time"] = pd.RangeIndex(len(df))

    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)


def synthetic(n: int = 3000, seed: int = 0, start_price: float = 2000.0,
              vol: float = 3.0) -> pd.DataFrame:
    """Generate a random-walk OHLC series that looks roughly like gold."""
    rng = np.random.default_rng(seed)
    close = start_price + rng.normal(0, vol, n).cumsum()
    close = np.maximum(close, 1.0)
    high = close + np.abs(rng.normal(0, vol / 2, n))
    low = close - np.abs(rng.normal(0, vol / 2, n))
    open_ = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame({
        "time": pd.date_range("2024-01-01", periods=n, freq="15min"),
        "open": open_, "high": high, "low": low, "close": close,
    })


def export_mt5_history(symbol: str, timeframe: str, n_bars: int, out_csv: str) -> str:
    """Pull the last `n_bars` bars of real history from MT5 into a CSV.

    Requires the MT5 terminal running. Import is local so the rest of this
    module works without MetaTrader5 installed.
    """
    from . import mt5_client as client  # local import to avoid hard dependency
    from .config import Config

    client.connect(Config())
    try:
        client.ensure_symbol(symbol)
        df = client.get_rates(symbol, timeframe, n_bars)
    finally:
        client.shutdown()
    df[["time", "open", "high", "low", "close"]].to_csv(out_csv, index=False)
    return out_csv


def _main() -> None:
    import argparse
    from .config import Config

    p = argparse.ArgumentParser(description="Export MT5 history to CSV for backtesting")
    p.add_argument("--symbol", default=Config().symbol)
    p.add_argument("--timeframe", default="M15")
    p.add_argument("--bars", type=int, default=5000)
    p.add_argument("--out", required=True)
    args = p.parse_args()
    export_mt5_history(args.symbol, args.timeframe, args.bars, args.out)
    print(f"Wrote {args.bars} bars of {args.symbol} {args.timeframe} to {args.out}")


if __name__ == "__main__":
    _main()
