"""Strategy logic: indicators and signal generation.

Top-down, multi-timeframe scalping with NO MT5 dependency, so it can be
unit-tested with synthetic data. The core decision (`entry_from_values`) lives
in ONE place and is used by both the live bot (per closed bar) and the
backtester, so the two can never silently diverge.

The approach
------------
1. HIGHER timeframe (e.g. H1) -> the broader TREND and key support/resistance.
2. LOWER  timeframe (e.g. M5) -> the EXECUTION signal: EMA(9)/EMA(20) sets the
   local trend and a fast RSI(7) times the entry at an extreme.

We BUY a pullback in an uptrend (RSI(7) crosses down to <= 20 while both the
lower- and higher-timeframe trends are up) and SELL a rally in a downtrend
(RSI(7) crosses up to >= 80 while both trends are down). A support/resistance
"room" check keeps us from entering right into the nearest higher-TF level.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def atr(df: pd.DataFrame, period: int) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    true_range = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False).mean()


def vwap(df: pd.DataFrame) -> pd.Series:
    """Volume Weighted Average Price, re-anchored each calendar day.

    Uses the typical price (H+L+C)/3 weighted by `tick_volume` when present (MT5
    rates include it). If no usable volume is available (some CSVs / synthetic
    data), it falls back to an unweighted running mean of the typical price, so
    the column is always defined and the filter degrades gracefully.
    """
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    if "tick_volume" in df and df["tick_volume"].astype(float).sum() > 0:
        vol = df["tick_volume"].astype(float)
    elif "real_volume" in df and df["real_volume"].astype(float).sum() > 0:
        vol = df["real_volume"].astype(float)
    else:
        vol = pd.Series(1.0, index=df.index)   # unweighted fallback
    day = pd.to_datetime(df["time"]).dt.normalize()
    cum_pv = (typical * vol).groupby(day).cumsum()
    cum_v = vol.groupby(day).cumsum()
    return cum_pv / cum_v


def adx(df: pd.DataFrame, period: int) -> pd.Series:
    """Average Directional Index (Wilder) — trend STRENGTH, not direction.

    High ADX (e.g. >= 25) means a strong directional move; low ADX means a
    range/chop. Used to pick the strategy regime, not to time entries.
    """
    high, low, close = df["high"], df["low"], df["close"]
    up = high.diff()
    down = -low.diff()
    plus_dm = ((up > down) & (up > 0)) * up.clip(lower=0)
    minus_dm = ((down > up) & (down > 0)) * down.clip(lower=0)
    prev_close = close.shift(1)
    tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
                   axis=1).max(axis=1)
    atr_ = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False).mean()


def use_mean_reversion(df_htf: pd.DataFrame, cfg) -> bool:
    """Decide the regime: True = RSI mean-reversion (ranging), False = trend-following.

    When `adaptive_regime` is on, a STRONG higher-TF trend (ADX >= threshold)
    selects trend-following; a weak/ranging market selects mean-reversion. When
    it is off, the fixed `mean_reversion` flag is used.
    """
    if not getattr(cfg, "adaptive_regime", False):
        return getattr(cfg, "mean_reversion", False)
    if df_htf is None or len(df_htf) < cfg.adx_period + 2:
        return False                       # not enough data -> default to trend
    adx_val = adx(df_htf, cfg.adx_period).iloc[-2]   # last CLOSED higher-TF bar
    if adx_val != adx_val:                 # NaN warmup
        return False
    return adx_val < cfg.trend_strength_adx


def _htf_adx_last(df_htf, cfg) -> float:
    """ADX of the last CLOSED higher-TF bar (NaN if not enough data)."""
    if df_htf is None or len(df_htf) < cfg.adx_period + 2:
        return float("nan")
    return float(adx(df_htf, cfg.adx_period).iloc[-2])


def compute_indicators(df: pd.DataFrame, cfg) -> pd.DataFrame:
    d = df.copy()
    d["ema_fast"] = ema(d["close"], cfg.ema_fast)   # EMA(9): local trend, fast leg
    d["ema_slow"] = ema(d["close"], cfg.ema_slow)   # EMA(20): local trend, slow leg
    d["rsi"] = rsi(d["close"], cfg.rsi_period)      # RSI(7): entry trigger at extremes
    d["atr"] = atr(d, cfg.atr_period)               # ATR: stop sizing + volatility gauge
    d["atr_avg"] = d["atr"].rolling(                # ATR baseline for the vol regime
        cfg.atr_avg_period, min_periods=max(5, cfg.atr_avg_period // 2)).mean()
    d["vwap"] = vwap(d)                              # VWAP: execution-side filter
    return d


def effective_reward_risk(atr_value: float, atr_avg: float, cfg) -> float:
    """Pick the reward:risk for current volatility.

    Returns `reward_risk_high_vol` (e.g. 1.0 -> 1:1) when ATR is elevated
    (>= high_vol_atr_mult x its average), otherwise `reward_risk` (e.g. 1.5 ->
    1:1.5). Falls back to the normal value while the ATR average is still warming
    up (NaN).
    """
    if (atr_avg == atr_avg and atr_avg > 0 and atr_value == atr_value
            and atr_value >= cfg.high_vol_atr_mult * atr_avg):
        return cfg.reward_risk_high_vol
    return cfg.reward_risk


# --- Higher-timeframe context ------------------------------------------------

def htf_trend(df_htf: pd.DataFrame, cfg) -> str | None:
    """Return 'up', 'down', or None — the broader trend from the higher TF.

    Uses the same EMA(9)/EMA(20) relationship on the last CLOSED higher-TF bar.
    """
    if len(df_htf) < cfg.ema_slow + 2:
        return None
    ef = ema(df_htf["close"], cfg.ema_fast).iloc[-2]   # -2 = last CLOSED htf bar
    es = ema(df_htf["close"], cfg.ema_slow).iloc[-2]
    if ef > es:
        return "up"
    if ef < es:
        return "down"
    return None


def sr_levels(df_htf: pd.DataFrame, cfg):
    """Return (support, resistance) from the last `sr_lookback` CLOSED htf bars.

    Resistance = highest high, support = lowest low. Returns (None, None) when
    the S/R filter is disabled or there is not enough history.
    """
    if not getattr(cfg, "use_sr_filter", False):
        return None, None
    closed = df_htf.iloc[:-1]                # drop the still-forming bar
    window = closed.tail(cfg.sr_lookback)
    if len(window) < 2:
        return None, None
    return float(window["low"].min()), float(window["high"].max())


def has_room(side: str, entry: float, tp_distance: float,
             support, resistance) -> bool:
    """True if the take-profit fits before the nearest higher-TF level."""
    if side == "buy":
        return resistance is None or (resistance - entry) >= tp_distance
    return support is None or (entry - support) >= tp_distance


def _vwap_sides(price: float, vwap_val: float, cfg):
    """Return (below_ok, above_ok) for the VWAP filter, with a tolerance band.

    A BUY needs `below_ok` (price at/under VWAP), a SELL needs `above_ok`. The
    `vwap_tolerance_pct` widens each side so price can sit a little on the
    "wrong" side of VWAP and still qualify (more RSI entries). 0 = strict;
    `use_vwap_filter = False` or a missing VWAP disables the gate entirely.
    """
    if not getattr(cfg, "use_vwap_filter", False) or vwap_val != vwap_val:  # NaN
        return True, True
    tol = abs(vwap_val) * getattr(cfg, "vwap_tolerance_pct", 0.0)
    return price <= vwap_val + tol, price >= vwap_val - tol


# --- Core decision rule (shared by live bot AND backtest) --------------------

def entry_from_values(ef_prev: float, es_prev: float, ef_last: float, es_last: float,
                      rsi_prev: float, rsi_last: float, price: float, vwap_val: float,
                      htf_dir: str | None, cfg):
    """Return 'buy', 'sell', or None — the execution-chart entry rule.

    Direction is gated by the HIGHER-timeframe trend (`htf_dir`). Within that, on
    the execution chart EITHER trigger can fire:

        * Trend trigger : an EMA(9)/EMA(20) crossover.
        * Reversion trigger: RSI(7) crossing into an extreme (<=20 / >=80).

    VWAP confirms each on its natural side: trend (EMA-cross) entries take the
    trade only on the trend side of VWAP, while RSI reversions fade the move from
    the opposite side. With `use_vwap_filter = False` (or VWAP undefined) the
    VWAP check is skipped.
    """
    # Trend mode uses the gentler trend pullback levels (e.g. 40/60), not the
    # strict range levels (30/70) — you're entering WITH the trend.
    buy_lvl = getattr(cfg, "trend_rsi_buy_level", cfg.rsi_buy_level)
    sell_lvl = getattr(cfg, "trend_rsi_sell_level", cfg.rsi_sell_level)
    cross_up = ef_prev <= es_prev and ef_last > es_last
    cross_dn = ef_prev >= es_prev and ef_last < es_last
    rsi_cross_os = rsi_prev > buy_lvl and rsi_last <= buy_lvl
    rsi_cross_ob = rsi_prev < sell_lvl and rsi_last >= sell_lvl

    below, above = _vwap_sides(price, vwap_val, cfg)

    buy = htf_dir == "up" and ((cross_up and above) or (rsi_cross_os and below))
    sell = htf_dir == "down" and ((cross_dn and below) or (rsi_cross_ob and above))
    if buy:
        return "buy"
    if sell:
        return "sell"
    return None


def meanrev_entry(rsi_prev: float, rsi_last: float, price: float, vwap_val: float, cfg):
    """Mean-reversion entry. Returns 'buy', 'sell', or None.

    BUY  when RSI crosses DOWN into oversold (<= rsi_buy_level) and price is at or
         below VWAP (fading the dip from below).
    SELL when RSI crosses UP into overbought (>= rsi_sell_level) and price is at or
         above VWAP (fading the rally from above).
    Ignores the higher-TF trend and news — only VWAP confirms. With
    `use_vwap_filter = False` (or VWAP undefined) the VWAP check is skipped.
    """
    os_ = rsi_prev > cfg.rsi_buy_level and rsi_last <= cfg.rsi_buy_level
    ob = rsi_prev < cfg.rsi_sell_level and rsi_last >= cfg.rsi_sell_level
    below, above = _vwap_sides(price, vwap_val, cfg)
    if os_ and below:
        return "buy"
    if ob and above:
        return "sell"
    return None


def meanrev_exit(side: str, rsi_last: float, cfg) -> bool:
    """True when an open mean-reversion trade should close ('exit the zone').

    A BUY (opened oversold) closes once RSI climbs back ABOVE rsi_buy_level; a
    SELL (opened overbought) closes once RSI falls back BELOW rsi_sell_level.
    """
    if rsi_last != rsi_last:        # NaN -> don't act
        return False
    if side == "buy":
        return rsi_last > cfg.rsi_buy_level
    return rsi_last < cfg.rsi_sell_level


def last_rsi(df: pd.DataFrame, cfg) -> float:
    """RSI of the most recent CLOSED bar (row -2), for live exit management."""
    if len(df) < cfg.rsi_period + 3:
        return float("nan")
    return float(rsi(df["close"], cfg.rsi_period).iloc[-2])


def generate_signal(df: pd.DataFrame, df_htf: pd.DataFrame, cfg):
    """Live-bot entry point.

    Returns (signal, atr_value, reward_risk, support, resistance, mode) for the
    most recent CLOSED execution bar (-2 = last closed, -3 = the one before).
    `mode` is "meanrev" or "trend" — which regime produced the signal (so the
    caller knows how to manage the exit). `df` is the execution timeframe;
    `df_htf` is the higher timeframe.
    """
    need = max(cfg.ema_slow, cfg.rsi_period, cfg.atr_period, cfg.atr_avg_period) + 3
    if len(df) < need:
        return None, float("nan"), cfg.reward_risk, None, None, "trend"

    d = compute_indicators(df, cfg)
    last, prev = d.iloc[-2], d.iloc[-3]
    rr = effective_reward_risk(last["atr"], last["atr_avg"], cfg)

    if use_mean_reversion(df_htf, cfg):
        # Ranging / weak trend -> RSI zones + VWAP only; no trend / S-R context.
        signal = meanrev_entry(prev["rsi"], last["rsi"], last["close"], last["vwap"], cfg)
        return signal, float(last["atr"]), rr, None, None, "meanrev"

    # Strong trend -> top-down trend-following.
    direction = htf_trend(df_htf, cfg)
    support, resistance = sr_levels(df_htf, cfg)
    signal = entry_from_values(
        prev["ema_fast"], prev["ema_slow"], last["ema_fast"], last["ema_slow"],
        prev["rsi"], last["rsi"], last["close"], last["vwap"], direction, cfg,
    )
    return signal, float(last["atr"]), rr, support, resistance, "trend"


def entry_debug(df: pd.DataFrame, df_htf: pd.DataFrame, cfg) -> str | None:
    """Explain why an RSI extreme did NOT produce an entry. None if RSI not in a zone."""
    need = max(cfg.ema_slow, cfg.rsi_period, cfg.atr_period, cfg.atr_avg_period) + 3
    if len(df) < need:
        return None
    d = compute_indicators(df, cfg)
    last, prev = d.iloc[-2], d.iloc[-3]
    rsi_l, rsi_p = last["rsi"], prev["rsi"]
    meanrev = use_mean_reversion(df_htf, cfg)
    # Range mode uses the strict 30/70 levels; trend mode the gentler 40/60.
    buy_lvl = cfg.rsi_buy_level if meanrev else getattr(cfg, "trend_rsi_buy_level", cfg.rsi_buy_level)
    sell_lvl = cfg.rsi_sell_level if meanrev else getattr(cfg, "trend_rsi_sell_level", cfg.rsi_sell_level)
    in_buy = rsi_l <= buy_lvl
    in_sell = rsi_l >= sell_lvl
    if not (in_buy or in_sell):
        return None

    htf = htf_trend(df_htf, cfg)
    price, vw = last["close"], last["vwap"]
    zone = "oversold(buy)" if in_buy else "overbought(sell)"
    head = f"RSI={rsi_l:.1f} {zone} | mode={'MEANREV' if meanrev else 'TREND'}"
    # Only show trend-strength context when the regime switch is actually in use.
    if getattr(cfg, "adaptive_regime", False):
        head += f" ADX={_htf_adx_last(df_htf, cfg):.0f}"
    if not meanrev:
        head += f" H1trend={htf}"

    crossed = (in_buy and rsi_p > buy_lvl) or (in_sell and rsi_p < sell_lvl)
    if not crossed:
        return head + " -> no fresh cross (RSI already in the zone on the prior bar)"

    if not meanrev:   # trend mode also needs the H1 trend to agree
        if in_buy and htf != "up":
            return head + " -> TREND mode won't buy oversold unless H1 is UP"
        if in_sell and htf != "down":
            return head + " -> TREND mode won't sell overbought unless H1 is DOWN"
    below_ok, above_ok = _vwap_sides(price, vw, cfg)   # honours the tolerance band
    if in_buy and not below_ok:
        return head + f" -> price {price:.2f} too far ABOVE VWAP {vw:.2f} (tol); buy needs price near/below VWAP"
    if in_sell and not above_ok:
        return head + f" -> price {price:.2f} too far BELOW VWAP {vw:.2f} (tol); sell needs price near/above VWAP"
    return head + " -> conditions met; blocked later by spread, open-slots, news or S/R"
