"""Configuration for the XAUUSD trading bot.

SAFETY FIRST
------------
* Start on a DEMO account. Keep `dry_run = True` until you have watched the bot
  behave sensibly for a long time. Do NOT put real money at risk until you have
  validated the strategy over many weeks of demo trading.
* Credentials: prefer environment variables over hardcoding them in this file.
  The bot reads MT5_LOGIN / MT5_PASSWORD / MT5_SERVER from the environment.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _env_int(name: str) -> int | None:
    val = os.getenv(name)
    return int(val) if val and val.strip().isdigit() else None


@dataclass
class Config:
    # --- Connection ---------------------------------------------------------
    login: int | None = _env_int("MT5_LOGIN")
    password: str | None = os.getenv("MT5_PASSWORD")
    server: str | None = os.getenv("MT5_SERVER")
    # Path to terminal64.exe. Only needed if MT5 cannot auto-detect the terminal.
    terminal_path: str | None = os.getenv("MT5_PATH") or None

    # --- Instrument ---------------------------------------------------------
    # The exact symbol name VARIES BY BROKER: XAUUSD, XAUUSD.r, GOLD, XAUUSDm, ...
    # Run `python -m xauusd_bot.bot --list-symbols` to find the name your broker uses.
    symbol: str = os.getenv("MT5_SYMBOL", "XAUUSD")
    # TOP-DOWN MULTI-TIMEFRAME SCALPING.
    #   * `htf_timeframe` = the HIGHER timeframe used to read the broader trend
    #     and key support/resistance (use H1 or M15).
    #   * `timeframe`     = the LOWER timeframe trades are EXECUTED on (M5 or M1).
    # Valid values: M1 M5 M15 M30 H1 H4 D1.
    htf_timeframe: str = "H1"       # broader trend + support/resistance
    timeframe: str = "M1"           # execution timeframe (drop to M1 for faster scalps)
    bars_to_load: int = 500         # execution-TF history loaded for indicators
    htf_bars_to_load: int = 300     # higher-TF history loaded for trend + S/R

    # --- Strategy parameters (EMA 9/20 trend + RSI 7 trigger) ---------------
    # Trend: fast EMA(9) vs slow EMA(20). Trigger: a fast RSI(7) reaching an
    # extreme. On the EXECUTION chart, EITHER an EMA(9/20) crossover OR an RSI(7)
    # extreme can trigger an entry, as long as it agrees with the higher-TF trend
    # and VWAP. (The same EMA(9/20) pair also reads the broader trend on the
    # higher timeframe.)
    # STRATEGY REGIME.
    # `adaptive_regime = True` (default): the bot picks the style automatically
    # from trend STRENGTH (ADX on the higher timeframe). A strong trend
    # (ADX >= trend_strength_adx) -> trend-following; a weak/ranging market
    # (ADX < trend_strength_adx) -> RSI mean-reversion. This avoids fighting
    # itself: it follows strong moves and fades chop.
    # If `adaptive_regime = False`, the fixed `mean_reversion` flag decides:
    #   True  -> always RSI mean-reversion (fade extremes, ignore trend/news/S-R)
    #   False -> always top-down trend-following (news/calendar gates active)
    adaptive_regime: bool = False       # OFF: ADX no longer switches strategy
    trend_strength_adx: float = 25.0    # (only used when adaptive_regime = True)
    adx_period: int = 14
    mean_reversion: bool = True         # always RSI mean-reversion (the previous rule)

    # Mean-reversion exits each trade on whichever comes first: RSI leaving the
    # zone (while the bot runs), the ATR take-profit, or the ATR stop. Trend
    # trades exit on their ATR take-profit / stop.

    ema_fast: int = 9
    ema_slow: int = 20
    rsi_period: int = 7
    rsi_buy_level: float = 30.0     # buy when RSI(7) drops to this or lower
    rsi_sell_level: float = 70.0    # sell when RSI(7) rises to this or higher
    atr_period: int = 14            # ATR sizes the STOP only — it is not a trend signal

    # --- VWAP filter (execution chart) --------------------------------------
    # Volume Weighted Average Price, re-anchored each day. It confirms each entry
    # on the natural side: trend (EMA-cross) entries must be on the trend side of
    # VWAP, while RSI mean-reversion entries fade the move from the other side.
    # Set `use_vwap_filter = False` to ignore VWAP entirely.
    use_vwap_filter: bool = True

    # --- Top-down support/resistance filter ---------------------------------
    # Support/resistance are the highest high / lowest low over the last
    # `sr_lookback` HIGHER-timeframe bars. A trade is skipped if its take-profit
    # would not fit before the nearest level (no room to run). Set
    # `use_sr_filter = False` to disable this gate entirely.
    use_sr_filter: bool = True
    sr_lookback: int = 50           # higher-TF bars used to mark S/R

    # --- Position sizing: balance-tiered (NOT percent-risk) -----------------
    # Lot size scales with account size: `lots_per_step` lots for every
    # `balance_per_step` of balance. Default = 0.01 lots per $100, increasing by
    # 0.01 per additional $100 (e.g. $250 -> 0.02, $1000 -> 0.10). Below $100 the
    # size is floored at the broker minimum (0.01) rather than skipped.
    balance_per_step: float = 100.0   # every this much balance...
    lots_per_step: float = 0.01       # ...adds this many lots

    # --- Stops / exits (tight, for scalping) --------------------------------
    atr_sl_mult: float = 1.5        # stop loss = atr_sl_mult * ATR away from entry
    # Reward:risk ADAPTS to volatility. Normal markets use `reward_risk` (1:1.5);
    # when the market is volatile the target tightens to `reward_risk_high_vol`
    # (1:1). "High volatility" = current ATR >= `high_vol_atr_mult` x its rolling
    # average over `atr_avg_period` bars.
    reward_risk: float = 1.0            # target (1:1)
    reward_risk_high_vol: float = 1.0   # high-vol target (kept equal -> flat 1:1 in all conditions)
    high_vol_atr_mult: float = 1.4      # ATR this many x its average => "high vol"
    atr_avg_period: int = 200           # bars for the ATR baseline (a long "recent normal")
    max_spread_points: int = 50     # skip entries when the spread is wider than this
    max_open_positions: int = 3     # up to THREE positions open at once

    # --- Account protection: 70% equity floor -------------------------------
    # Halt ALL trading (and close any open bot position) once equity reaches or
    # nears `equity_floor_frac` of the balance recorded when the bot started.
    # 0.70 = stop once the account has fallen to 70% of its starting balance.
    # `equity_floor_buffer` makes it trigger slightly BEFORE the floor ("near").
    equity_floor_frac: float = 0.70   # protective floor as a fraction of start balance
    equity_floor_buffer: float = 0.02 # halt this much earlier (so ~72% here)

    # --- News bias (LIVE only) ----------------------------------------------
    # Steers trade DIRECTION from recent USD/gold headlines: a bullish-for-gold
    # read allows only BUYS, bearish allows only SELLS, neutral leaves the
    # technical signal unconstrained (so news never fully stops the bot). A
    # headline is ignored until it is `news_min_age_minutes` old, so the bot
    # acts on the news only AFTER the initial spike. Backtests ignore this.
    #
    # The bot tries each feed in order and uses the first that works in your
    # region (FXStreet is geo-blocked in some places; Google News and
    # Investing.com generally are not). Reorder or trim this list as needed.
    use_news_bias: bool = True
    news_rss_urls: tuple = (
        "https://www.fxstreet.com/rss/news",   # PRIMARY (needs a VPN in blocked regions)
        "https://news.google.com/rss/search?q=%22gold+price%22+OR+XAUUSD+OR+%22Federal+Reserve%22+OR+%22US+dollar%22+when:2d&hl=en-US&gl=US&ceid=US:en",
    )
    news_min_age_minutes: int = 5     # wait this long after a headline before acting
    news_lookback_hours: float = 6.0  # how far back headlines still count
    news_refresh_seconds: int = 120   # how often to re-pull the feed
    news_fetch_timeout: int = 8       # seconds before giving up on a fetch
    news_threshold: float = 0.5       # |combined score| above this commits to a direction

    # --- Economic calendar bias (FXStreet API, LIVE only) -------------------
    # Reads scheduled USD releases as JSON and turns Actual-vs-Consensus into a
    # gold direction (beat = bearish, miss = bullish), weighted by impact and
    # recency, with the same 5-min settle delay. Its score is ADDED to the
    # headline score before deciding the overall bias. The page is JS-rendered
    # so we use the data API (geo-blocked like the rest of FXStreet -> VPN).
    # Reuses news_min_age_minutes / news_lookback_hours / news_refresh_seconds /
    # news_fetch_timeout / news_threshold. Verify with `python -m xauusd_bot.econ_calendar`.
    use_calendar_bias: bool = True
    calendar_api_url: str = (
        "https://calendar-api.fxstreet.com/en/api/v1/eventDates/{start}/{end}"
        "?volatilities=NONE&volatilities=LOW&volatilities=MEDIUM&volatilities=HIGH"
        "&countries=US"
    )
    calendar_currencies: tuple = ("USD",)   # which event currencies to read

    # --- Execution ----------------------------------------------------------
    magic: int = 20260607           # tag used to identify this bot's own trades
    deviation: int = 20             # max acceptable slippage, in points
    poll_seconds: int = 5           # how often to poll for a newly closed bar

    # --- Master safety switch ----------------------------------------------
    dry_run: bool = False            # True = log intended trades, send NO orders
