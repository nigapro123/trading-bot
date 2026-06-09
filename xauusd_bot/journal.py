"""Append-only CSV journal of closed live/demo trades.

Written by the live bot as positions close, then read by analyze_live.py and
scored with the SAME metrics module as the backtester — so you can compare
"how it traded live" against "how the backtest said it should".
"""
from __future__ import annotations

import csv
import os

FIELDS = ["time", "ticket", "side", "lots", "pnl", "r", "equity_after"]


class Journal:
    def __init__(self, path: str = "live_trades.csv"):
        self.path = path
        if not os.path.exists(path):
            with open(path, "w", newline="") as f:
                csv.DictWriter(f, fieldnames=FIELDS).writeheader()

    def append(self, row: dict) -> None:
        clean = {k: row.get(k, "") for k in FIELDS}
        with open(self.path, "a", newline="") as f:
            csv.DictWriter(f, fieldnames=FIELDS).writerow(clean)
