"""Thin wrapper around the MetaTrader5 Python API.

Everything that talks to the broker terminal lives here so the rest of the bot
stays broker-agnostic and easy to test.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import MetaTrader5 as mt5
import pandas as pd

log = logging.getLogger(__name__)

TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}


def connect(cfg) -> None:
    """Initialise the terminal and (optionally) log in."""
    kwargs = {}
    if cfg.terminal_path:
        kwargs["path"] = cfg.terminal_path
    if cfg.login and cfg.password and cfg.server:
        kwargs.update(login=cfg.login, password=cfg.password, server=cfg.server)

    if not mt5.initialize(**kwargs):
        raise RuntimeError(f"MT5 initialize() failed: {mt5.last_error()}")

    acc = mt5.account_info()
    if acc is None:
        raise RuntimeError(f"Could not read account info: {mt5.last_error()}")
    log.info(
        "Connected. account=%s server=%s balance=%.2f equity=%.2f trade_allowed=%s",
        acc.login, acc.server, acc.balance, acc.equity, acc.trade_allowed,
    )


def shutdown() -> None:
    mt5.shutdown()


def list_symbols(filter_text: str = "XAU") -> list[str]:
    """Return symbol names matching `filter_text` (useful to find the gold symbol)."""
    symbols = mt5.symbols_get()
    if symbols is None:
        return []
    return [s.name for s in symbols if filter_text.upper() in s.name.upper()]


def ensure_symbol(symbol: str):
    """Make sure the symbol is selected in Market Watch and return its info."""
    info = mt5.symbol_info(symbol)
    if info is None:
        raise RuntimeError(
            f"Symbol {symbol!r} not found. Try list_symbols() to see the exact name "
            f"your broker uses (it may be 'XAUUSD.r', 'GOLD', etc.)."
        )
    if not info.visible and not mt5.symbol_select(symbol, True):
        raise RuntimeError(f"Failed to select {symbol!r} in Market Watch.")
    return mt5.symbol_info(symbol)


def account_info():
    return mt5.account_info()


def get_rates(symbol: str, timeframe: str, n: int) -> pd.DataFrame:
    """Return the last `n` bars as a DataFrame (oldest first).

    NOTE: the final row is the *currently forming* bar. Use row -2 for the most
    recent *closed* bar when generating signals.
    """
    tf = TIMEFRAME_MAP[timeframe]
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, n)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"No rates returned for {symbol}: {mt5.last_error()}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df


def get_tick(symbol: str):
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise RuntimeError(f"No tick for {symbol}: {mt5.last_error()}")
    return tick


def open_positions(symbol: str, magic: int) -> list:
    """Only positions opened by THIS bot (matched on the magic number)."""
    positions = mt5.positions_get(symbol=symbol)
    if positions is None:
        return []
    return [p for p in positions if p.magic == magic]


def closed_positions(symbol: str, magic: int, lookback_days: int = 3) -> list:
    """Closed bot positions from MT5 deal history (restart-proof, broker truth).

    Returns one dict per CLOSED position matched on `magic`:
        {ticket, time (ISO), side, lots, pnl}
    Reads the permanent deal history rather than in-memory state, so trades that
    closed while the bot was restarted are still captured.
    """
    deals = mt5.history_deals_get(datetime.now() - timedelta(days=lookback_days),
                                  datetime.now() + timedelta(days=1))
    if not deals:
        return []
    by_pos: dict = {}
    for d in deals:
        if d.symbol != symbol or d.magic != magic:
            continue
        by_pos.setdefault(d.position_id, []).append(d)

    result = []
    for pid, ds in by_pos.items():
        # A position is closed once it has a non-IN deal (OUT/INOUT/OUT_BY).
        if not any(getattr(d, "entry", 0) != 0 for d in ds):
            continue
        pnl = sum(d.profit + getattr(d, "swap", 0.0) + getattr(d, "commission", 0.0)
                  for d in ds)
        in_deal = next((d for d in ds if getattr(d, "entry", 0) == 0), ds[0])
        side = "buy" if in_deal.type == 0 else "sell"   # DEAL_TYPE_BUY == 0
        close_time = max(d.time for d in ds)
        result.append({
            "ticket": pid,
            "time": datetime.fromtimestamp(close_time).isoformat(timespec="seconds"),
            "side": side, "lots": in_deal.volume, "pnl": round(pnl, 2),
        })
    return result


def _filling_mode(symbol_info):
    """Pick an order filling mode the symbol actually supports."""
    mode = symbol_info.filling_mode
    if mode & 1:        # SYMBOL_FILLING_FOK
        return mt5.ORDER_FILLING_FOK
    if mode & 2:        # SYMBOL_FILLING_IOC
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


def market_order(symbol_info, side: str, volume: float, sl: float, tp: float,
                 deviation: int, magic: int, comment: str = "xauusd-bot"):
    """Send a market order. `side` is 'buy' or 'sell'."""
    symbol = symbol_info.name
    tick = get_tick(symbol)
    is_buy = side == "buy"
    price = tick.ask if is_buy else tick.bid
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": float(volume),
        "type": mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
        "price": price,
        "sl": round(sl, symbol_info.digits),
        "tp": round(tp, symbol_info.digits),
        "deviation": deviation,
        "magic": magic,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": _filling_mode(symbol_info),
    }
    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        log.error("order_send failed: %s | request=%s", result, request)
    else:
        log.info("ORDER FILLED %s %.2f lots @ %.3f sl=%.3f tp=%.3f",
                 side.upper(), volume, price, sl, tp)
    return result


def position_realized_pnl(position_ticket: int):
    """Sum realized profit (incl. swap/commission) for a closed position.

    Returns None if no deals are found for the ticket yet.
    """
    deals = mt5.history_deals_get(position=position_ticket)
    if not deals:
        return None
    total = 0.0
    for d in deals:
        total += d.profit + getattr(d, "swap", 0.0) + getattr(d, "commission", 0.0)
    return total


def close_position(position, deviation: int, magic: int):
    """Close an existing position with an opposite market order."""
    symbol = position.symbol
    info = mt5.symbol_info(symbol)
    tick = get_tick(symbol)
    is_long = position.type == mt5.POSITION_TYPE_BUY
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": position.volume,
        "type": mt5.ORDER_TYPE_SELL if is_long else mt5.ORDER_TYPE_BUY,
        "position": position.ticket,
        "price": tick.bid if is_long else tick.ask,
        "deviation": deviation,
        "magic": magic,
        "comment": "xauusd-bot-close",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": _filling_mode(info),
    }
    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        log.error("close failed: %s", result)
    else:
        log.info("CLOSED position %s", position.ticket)
    return result
