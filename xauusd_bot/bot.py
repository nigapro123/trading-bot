"""Entry point: the live (or dry-run) trading loop for XAUUSD.

Usage
-----
    python -m xauusd_bot.bot                # run the loop (dry_run per config)
    python -m xauusd_bot.bot --list-symbols # print gold-like symbols and exit

The loop polls for a newly CLOSED bar; when one appears it evaluates the
strategy once and either opens a position (if flat) or leaves the open position
to be managed by its stop loss / take profit.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime

from . import mt5_client as client
from . import news
from . import risk
from . import strategy
from .config import Config
from .journal import Journal

log = logging.getLogger("xauusd_bot")


@dataclass
class LiveState:
    """Tracks the bot's own open positions so closures can be journaled."""
    journal: Journal
    known: dict = field(default_factory=dict)   # ticket -> {risk, side, lots, mode}
    pending_risk: float | None = None           # risk_amount of the trade just sent
    pending_mode: str | None = None             # "meanrev"/"trend" of the trade just sent


def _reconcile(cfg: Config, state: LiveState, positions, equity: float) -> None:
    """Detect newly opened and newly closed bot positions; journal closes."""
    current = {p.ticket: p for p in positions}

    for ticket, p in current.items():
        if ticket not in state.known:
            side = "buy" if p.type == 0 else "sell"  # 0 == POSITION_TYPE_BUY
            state.known[ticket] = {"risk": state.pending_risk, "side": side,
                                   "lots": p.volume, "mode": state.pending_mode}
            state.pending_risk = None
            state.pending_mode = None

    for ticket in list(state.known):
        if ticket not in current:
            realized = client.position_realized_pnl(ticket)
            info = state.known.pop(ticket)
            if realized is not None:
                risk_amt = info.get("risk")
                state.journal.append({
                    "time": datetime.now().isoformat(timespec="seconds"),
                    "ticket": ticket, "side": info.get("side", ""),
                    "lots": info.get("lots", ""), "pnl": round(realized, 2),
                    "r": round(realized / risk_amt, 3) if risk_amt else "",
                    "equity_after": round(equity, 2),
                })
                log.info("Journaled closed trade %s: pnl=%.2f", ticket, realized)


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def on_new_bar(cfg: Config, symbol_info, guard: risk.EquityFloorGuard, df, df_htf,
               state: LiveState, news_feed=None) -> None:
    acc = client.account_info()
    if acc is None:
        log.error("No account info; skipping bar.")
        return

    positions = client.open_positions(cfg.symbol, cfg.magic)
    _reconcile(cfg, state, positions, acc.equity)

    if guard.trading_halted(acc.equity):
        # 70% equity floor reached: stop trading and close any open bot position.
        for p in positions:
            if cfg.dry_run:
                log.warning("[DRY RUN] equity floor: would CLOSE position %s", p.ticket)
            else:
                client.close_position(p, cfg.deviation, cfg.magic)
        return

    # Active RSI-zone exit for MEAN-REVERSION trades only (tagged when opened).
    # Trend trades are left to their SL/TP. Positions whose mode is unknown
    # (e.g. after a restart) are left to SL/TP too.
    if positions:
        rsi_now = strategy.last_rsi(df, cfg)
        for p in positions:
            if state.known.get(p.ticket, {}).get("mode") != "meanrev":
                continue
            side = "buy" if p.type == 0 else "sell"   # 0 == POSITION_TYPE_BUY
            if strategy.meanrev_exit(side, rsi_now, cfg):
                if cfg.dry_run:
                    log.info("[DRY RUN] RSI %.1f left the zone: would CLOSE %s position %s",
                             rsi_now, side.upper(), p.ticket)
                else:
                    client.close_position(p, cfg.deviation, cfg.magic)
        if not cfg.dry_run:
            positions = client.open_positions(cfg.symbol, cfg.magic)

    signal, atr_value, reward_risk, support, resistance, mode = \
        strategy.generate_signal(df, df_htf, cfg)

    if signal:
        vol = "HIGH-vol" if reward_risk == cfg.reward_risk_high_vol else "normal-vol"
        log.info("Signal: %s [%s] (ATR=%.3f, R:R=1:%.2g %s, open positions=%d/%d)",
                 signal.upper(), mode, atr_value, reward_risk, vol,
                 len(positions), cfg.max_open_positions)
    else:
        why = strategy.entry_debug(df, df_htf, cfg)   # explain a skipped RSI extreme
        if why:
            log.info("No entry — %s", why)

    # Up to `max_open_positions` at once; beyond that, let SL/TP manage exits.
    if len(positions) >= cfg.max_open_positions:
        return
    if not signal or atr_value != atr_value:  # NaN check
        return

    # News-bias direction gate (TREND trades only). Bullish-for-gold news blocks
    # sells, bearish blocks buys; neutral imposes no constraint.
    if mode == "trend" and cfg.use_news_bias and news_feed is not None:
        bias, score, latest = news_feed.current()
        if (bias == "bullish" and signal == "sell") or (bias == "bearish" and signal == "buy"):
            log.info("News bias %s (%.2f) blocks %s. Latest: %s",
                     bias.upper(), score, signal.upper(), latest)
            return
        if bias != "neutral":
            log.info("News bias %s (%.2f) allows %s. Latest: %s",
                     bias.upper(), score, signal.upper(), latest)

    # Spread filter.
    tick = client.get_tick(cfg.symbol)
    spread_points = round((tick.ask - tick.bid) / symbol_info.point)
    if spread_points > cfg.max_spread_points:
        log.info("Spread %d pts > limit %d; skipping entry.",
                 spread_points, cfg.max_spread_points)
        return

    entry = tick.ask if signal == "buy" else tick.bid
    sl, tp, stop_distance = risk.stop_and_target(
        signal, entry, atr_value, cfg.atr_sl_mult, reward_risk
    )

    # Top-down support/resistance gate — TREND trades only (mean-reversion has
    # no S/R context). Both modes keep the ATR take-profit AND stop on the order.
    if mode == "trend":
        tp_distance = reward_risk * stop_distance
        if not strategy.has_room(signal, entry, tp_distance, support, resistance):
            log.info("No room to nearest S/R level; skipping %s entry @ %.3f.",
                     signal.upper(), entry)
            return

    volume = risk.lot_size_by_balance(acc.balance, cfg, symbol_info)
    if volume <= 0:
        return

    if cfg.dry_run:
        log.info(
            "[DRY RUN] would %s %.2f lots @ %.3f  sl=%.3f tp=%.3f [%s] (balance %.2f)",
            signal.upper(), volume, entry, sl, tp, mode, acc.balance,
        )
        state.pending_mode = mode
        return

    result = client.market_order(symbol_info, signal, volume, sl, tp,
                                 cfg.deviation, cfg.magic)
    if result is not None and result.retcode == 10009:  # TRADE_RETCODE_DONE
        # Stash the intended risk + mode so _reconcile can tag this trade later.
        state.pending_risk = stop_distance * symbol_info.trade_contract_size * volume
        state.pending_mode = mode


def run(cfg: Config) -> None:
    _setup_logging()
    if cfg.dry_run:
        log.warning("DRY RUN is ON — signals will be logged but NO orders sent.")
    else:
        log.warning("LIVE MODE — real orders WILL be sent. Make sure this is a demo!")

    client.connect(cfg)
    try:
        symbol_info = client.ensure_symbol(cfg.symbol)
        guard = risk.EquityFloorGuard(
            client.account_info().balance, cfg.equity_floor_frac, cfg.equity_floor_buffer
        )
        state = LiveState(journal=Journal("live_trades.csv"))
        news_feed = news.NewsBiasFeed(cfg) if cfg.use_news_bias else None
        last_bar_time = None
        log.info("Trading %s on %s (trend/S-R from %s, news bias %s). Polling every %ds. Ctrl+C to stop.",
                 cfg.symbol, cfg.timeframe, cfg.htf_timeframe,
                 "ON" if news_feed else "off", cfg.poll_seconds)

        while True:
            try:
                df = client.get_rates(cfg.symbol, cfg.timeframe, cfg.bars_to_load)
                df_htf = client.get_rates(cfg.symbol, cfg.htf_timeframe, cfg.htf_bars_to_load)
                closed_bar_time = df.iloc[-2]["time"]
                if closed_bar_time != last_bar_time:
                    last_bar_time = closed_bar_time
                    on_new_bar(cfg, symbol_info, guard, df, df_htf, state, news_feed)
            except Exception as exc:  # keep the loop alive on transient errors
                log.exception("Error in trading loop: %s", exc)
            time.sleep(cfg.poll_seconds)
    except KeyboardInterrupt:
        log.info("Stopped by user.")
    finally:
        client.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description="XAUUSD trading bot")
    parser.add_argument("--list-symbols", action="store_true",
                        help="List gold-like symbols offered by your broker and exit.")
    args = parser.parse_args()
    cfg = Config()

    if args.list_symbols:
        _setup_logging()
        client.connect(cfg)
        try:
            names = client.list_symbols("XAU")
            print("Gold-like symbols:", names or "(none found — try a different filter)")
        finally:
            client.shutdown()
        sys.exit(0)

    run(cfg)


if __name__ == "__main__":
    main()
