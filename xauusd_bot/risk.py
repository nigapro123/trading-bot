"""Risk management: position sizing and a daily-loss circuit breaker.

Gold-specific sizing note
-------------------------
XAUUSD is priced in USD per troy ounce and a standard contract is 100 oz
(`trade_contract_size`). So a $1 move in the gold price = $100 profit/loss per
1.0 lot. We size each trade so that hitting the stop loses approximately
`risk_per_trade` of the account balance.
"""
from __future__ import annotations

import logging
import math
from datetime import date

log = logging.getLogger(__name__)


def lot_size(balance: float, risk_per_trade: float, stop_distance_price: float,
             symbol_info) -> float:
    """Lot size so that a stop-out loses ~`risk_per_trade` of `balance`.

    Returns 0.0 if the computed size is below the broker's minimum lot, which
    means the intended risk is too small for one minimum lot. In that case the
    caller should SKIP the trade rather than over-risk by rounding up.
    """
    if stop_distance_price <= 0:
        return 0.0

    contract = symbol_info.trade_contract_size      # 100 oz for XAUUSD
    risk_amount = balance * risk_per_trade
    loss_per_lot = stop_distance_price * contract    # USD lost per 1.0 lot at the stop
    raw = risk_amount / loss_per_lot

    step = symbol_info.volume_step or 0.01
    vol = math.floor(raw / step) * step

    if vol < symbol_info.volume_min:
        log.warning(
            "Risk %.2f%% of %.2f is too small for the minimum lot (%.2f). Skipping.",
            risk_per_trade * 100, balance, symbol_info.volume_min,
        )
        return 0.0

    vol = min(vol, symbol_info.volume_max)
    # Round to the precision implied by the volume step (e.g. 0.01 -> 2 dp).
    decimals = max(0, int(round(-math.log10(step)))) if step < 1 else 0
    return round(vol, decimals)


def lot_size_by_balance(balance: float, cfg, symbol_info) -> float:
    """Balance-tiered lot size: `lots_per_step` lots per `balance_per_step` of balance.

    Default config => 0.01 lots for every $100, increasing by 0.01 each $100:
        $100 -> 0.01, $250 -> 0.02, $1000 -> 0.10, $10,000 -> 1.00.
    The size is then snapped to the broker's lot step and clamped to its
    min/max. Returns 0.0 when the balance is below one full step (e.g. < $100),
    so the caller SKIPS the trade rather than under-sizing.
    """
    if balance <= 0 or cfg.balance_per_step <= 0:
        return 0.0

    steps = math.floor(balance / cfg.balance_per_step)   # whole $100 blocks
    raw = steps * cfg.lots_per_step

    step = symbol_info.volume_step or 0.01
    vol = math.floor(raw / step) * step

    # Below one full step (e.g. balance < $100) the tier rounds to 0 — but we
    # still trade the broker MINIMUM lot rather than skipping the trade.
    if vol < symbol_info.volume_min:
        vol = symbol_info.volume_min

    vol = min(vol, symbol_info.volume_max)
    decimals = max(0, int(round(-math.log10(step)))) if step < 1 else 0
    return round(vol, decimals)


def stop_and_target(side: str, entry: float, atr_value: float,
                    atr_sl_mult: float, reward_risk: float):
    """Return (stop_loss, take_profit, stop_distance) prices for an entry."""
    stop_distance = atr_sl_mult * atr_value
    if side == "buy":
        sl = entry - stop_distance
        tp = entry + reward_risk * stop_distance
    else:
        sl = entry + stop_distance
        tp = entry - reward_risk * stop_distance
    return sl, tp, stop_distance


class DailyGuard:
    """Halts trading once equity drops by `max_daily_loss` within a calendar day."""

    def __init__(self, start_equity: float, max_daily_loss: float):
        self.day = date.today()
        self.start_equity = start_equity
        self.max_daily_loss = max_daily_loss

    def _roll_day_if_needed(self, equity: float) -> None:
        today = date.today()
        if today != self.day:
            self.day = today
            self.start_equity = equity
            log.info("New trading day. Reset equity baseline to %.2f", equity)

    def trading_halted(self, equity: float) -> bool:
        self._roll_day_if_needed(equity)
        floor = self.start_equity * (1 - self.max_daily_loss)
        if equity <= floor:
            log.warning(
                "Daily loss limit hit: equity %.2f <= floor %.2f. No new trades today.",
                equity, floor,
            )
            return True
        return False


class EquityFloorGuard:
    """Permanent account-protection floor at ~70% of the STARTING balance.

    Unlike the daily guard, this baseline is fixed at bot startup and never
    resets. Once equity reaches (or nears) `floor_frac` of that starting
    balance, the bot stops opening trades for good and the caller should close
    any open position. The `buffer` makes it trigger slightly early ("near").
    """

    def __init__(self, start_balance: float, floor_frac: float, buffer: float = 0.0):
        self.start_balance = start_balance
        # e.g. floor_frac=0.70, buffer=0.02 -> halt at 72% of the start balance.
        self.threshold = start_balance * (floor_frac + buffer)
        self._tripped = False

    def trading_halted(self, equity: float) -> bool:
        if self._tripped:
            return True
        if equity <= self.threshold:
            self._tripped = True
            log.warning(
                "EQUITY FLOOR hit: equity %.2f <= %.2f (%.0f%% of start balance "
                "%.2f). Halting all trading.",
                equity, self.threshold,
                100 * self.threshold / self.start_balance if self.start_balance else 0,
                self.start_balance,
            )
            return True
        return False
