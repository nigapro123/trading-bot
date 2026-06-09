"""Connection diagnostic for the XAUUSD bot on a Pepperstone MT5 demo account.

Run this FIRST, before the bot, to confirm everything is wired up:

    python -m xauusd_bot.connect_test

It performs read-only checks only — it never sends an order. It will:
  1. Initialise / attach to your running MT5 terminal.
  2. Print terminal + account info and whether algo-trading is allowed.
  3. List the gold-like symbols your broker offers and select the configured one.
  4. Pull recent bars + the live tick to prove the data feed works.

Every check prints a clear [OK] / [FAIL] line and ends with a verdict so you
know exactly what to fix before running the bot.
"""
from __future__ import annotations

import sys

try:
    import MetaTrader5 as mt5
except Exception as exc:  # pragma: no cover - only triggers off-Windows / no pkg
    print("[FAIL] Could not import the MetaTrader5 package.")
    print("       Install it on the SAME Windows machine as the terminal:")
    print("       python -m pip install MetaTrader5 pandas numpy")
    print(f"       (import error: {exc})")
    sys.exit(1)

from .config import Config
from . import mt5_client as client


def _line(ok: bool, msg: str) -> bool:
    print(f"[{'OK' if ok else 'FAIL'}] {msg}")
    return ok


def main() -> None:
    cfg = Config()
    failures: list[str] = []

    print("=" * 64)
    print("  XAUUSD bot — Pepperstone MT5 connection test")
    print("=" * 64)
    print(f"Configured symbol : {cfg.symbol}")
    print(f"Configured TF     : {cfg.timeframe}")
    print(f"Login from env    : {'yes' if cfg.login else 'no (will attach to open terminal)'}")
    print("-" * 64)

    # 1) Initialise / attach -------------------------------------------------
    try:
        client.connect(cfg)
        _line(True, "Terminal initialised and account info read.")
    except Exception as exc:
        _line(False, f"Could not connect to MT5: {exc}")
        print()
        print("  Fix: make sure the Pepperstone MT5 *desktop terminal* is OPEN")
        print("  and logged in to your demo account before running this. If it is")
        print("  open and this still fails, set MT5_LOGIN / MT5_PASSWORD / MT5_SERVER")
        print("  (see the quickstart) and try again.")
        sys.exit(1)

    # 2) Terminal + account details -----------------------------------------
    term = mt5.terminal_info()
    acc = mt5.account_info()
    if term is not None:
        _line(term.connected, f"Terminal connected to broker: {term.connected}") or \
            failures.append("terminal not connected to broker")
        algo_ok = _line(term.trade_allowed,
                        f"Algo-trading enabled in terminal: {term.trade_allowed}")
        if not algo_ok:
            failures.append("algo-trading disabled")
            print("       -> In MT5: Tools > Options > Expert Advisors, tick")
            print("          'Allow Algo Trading'; also toggle the 'Algo Trading'")
            print("          toolbar button so it is green.")
    if acc is not None:
        demo = getattr(acc, "trade_mode", None) == 0  # 0 == ACCOUNT_TRADE_MODE_DEMO
        _line(True, f"Account {acc.login} on {acc.server} "
                    f"({'DEMO' if demo else 'NOT demo — be careful!'})")
        print(f"       balance={acc.balance:.2f} {acc.currency}  "
              f"equity={acc.equity:.2f}  leverage=1:{acc.leverage}")
        if not demo:
            print("       -> This does NOT look like a demo account. Validate the bot")
            print("          on a demo account before risking real money.")

    # 3) Symbols -------------------------------------------------------------
    gold_like = client.list_symbols("XAU")
    _line(bool(gold_like), f"Gold-like symbols offered: {gold_like or '(none found)'}")
    if not gold_like:
        failures.append("no XAU symbols")
        print("       -> In MT5 Market Watch, right-click > 'Symbols' / 'Show All'")
        print("          to reveal every instrument the server offers.")

    try:
        info = client.ensure_symbol(cfg.symbol)
        _line(True, f"Selected '{cfg.symbol}': digits={info.digits} "
                    f"point={info.point} contract_size={info.trade_contract_size} "
                    f"min_lot={info.volume_min} lot_step={info.volume_step}")
    except Exception as exc:
        info = None
        _line(False, f"Symbol '{cfg.symbol}' unusable: {exc}")
        failures.append("configured symbol not found")
        if gold_like:
            print(f"       -> Set MT5_SYMBOL to one of: {gold_like}")

    # 4) Data feed + live tick ----------------------------------------------
    if info is not None:
        try:
            df = client.get_rates(cfg.symbol, cfg.timeframe, cfg.bars_to_load)
            last_closed = df.iloc[-2]
            _line(len(df) >= 60,
                  f"Pulled {len(df)} {cfg.timeframe} bars. "
                  f"Last CLOSED bar {last_closed['time']} close={last_closed['close']:.3f}")
            if len(df) < 60:
                failures.append("not enough history bars")
        except Exception as exc:
            _line(False, f"Could not pull bars: {exc}")
            failures.append("no rate history")

        try:
            tick = client.get_tick(cfg.symbol)
            spread_pts = round((tick.ask - tick.bid) / info.point)
            within = spread_pts <= cfg.max_spread_points
            _line(True, f"Live tick: bid={tick.bid:.3f} ask={tick.ask:.3f} "
                        f"spread={spread_pts} pts (entry limit {cfg.max_spread_points})")
            if not within:
                print("       Note: spread currently above the entry filter — normal")
                print("       outside active hours; the bot will simply skip entries.")
        except Exception as exc:
            _line(False, f"No live tick: {exc}")
            failures.append("no live tick")

    # Verdict ----------------------------------------------------------------
    print("-" * 64)
    if failures:
        print(f"RESULT: {len(failures)} issue(s) to fix: {', '.join(failures)}")
        print("Re-run this test until every line shows [OK], then start the bot")
        print("in dry-run with:  python -m xauusd_bot.bot")
        code = 1
    else:
        print("RESULT: All checks passed. You're connected to your demo feed.")
        print("Next:  python -m xauusd_bot.bot   (starts in DRY-RUN — no orders sent)")
        code = 0
    print("=" * 64)

    client.shutdown()
    sys.exit(code)


if __name__ == "__main__":
    main()
