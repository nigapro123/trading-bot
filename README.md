# XAUUSD Trading Bot (MetaTrader 5)

A small, readable trading bot for the **gold / USD (XAUUSD)** pair. It connects to
a MetaTrader 5 broker account, evaluates an EMA-crossover + RSI strategy on closed
bars, sizes positions by a fixed percent-risk rule, and manages exits with
ATR-based stop loss / take profit.

> ⚠️ **This is an educational starting template, not a profitable strategy.**
> Automated trading can lose money quickly. Run it on a **demo account** with
> `dry_run = True` and watch it for a long time before considering anything else.
> Nothing here is financial advice.

## What it does

> **Adaptive regime (default, `adaptive_regime = True`).** The bot reads trend
> STRENGTH from **ADX on the H1 chart** and picks the strategy per bar:
>
> - **Strong trend** (ADX ≥ `trend_strength_adx`, 25) → **trend-following**: buys
>   in H1 uptrends / sells in H1 downtrends, via EMA(9/20) crosses or trend-aligned
>   RSI entries, with the news/calendar and S/R filters active. Exits on ATR
>   take-profit / stop.
> - **Weak / ranging** (ADX < threshold) → **RSI mean-reversion**: BUYS when RSI(7)
>   crosses ≤ `rsi_buy_level`, SELLS when it crosses ≥ `rsi_sell_level` (VWAP only,
>   ignores trend/news/S-R), and closes when RSI leaves the zone (or ATR TP/stop).
>
> Each trade is tagged with the mode that opened it, so it's exited the right way.
> Set `adaptive_regime = False` to force a single style with the `mean_reversion`
> flag (`True` = always mean-reversion, `False` = always trend-following). The
> per-filter details below apply to the trend-following side.

1. Connects to your MT5 terminal/account.
2. **Top-down, two timeframes.** It reads the broader trend and key
   support/resistance from a *higher* timeframe (H1 by default) and executes on
   a *lower* one (M5 by default; drop to M1 for faster scalps).
3. **Trend filter (higher TF):** EMA9 vs EMA20 on H1 sets the allowed
   direction — only longs in an uptrend, only shorts in a downtrend.
4. **Execution triggers (lower TF):** within the allowed direction, EITHER an
   EMA(9/20) crossover OR an RSI(7) extreme (crossing ≤ 20 to buy / ≥ 80 to
   sell) opens a trade. (ATR(14) is also computed, but only to size the stop.)
5. **VWAP filter (lower TF):** a daily-anchored VWAP confirms each trigger on its
   natural side — trend crosses on the trend side of VWAP, RSI reversions from
   the other side. Toggle with `use_vwap_filter`.
6. **Support/resistance gate:** skips an entry if its take-profit wouldn't fit
   before the nearest higher-TF level (no room to run). Toggle with
   `use_sr_filter`.
7. **News bias (live only):** pulls news from RSS (FXStreet first, Google News
   as a no-VPN fallback) **and** scheduled USD releases from FXStreet's economic
   calendar API. Headlines are scored bullish/bearish-for-gold; calendar releases
   add a **data surprise** read (Actual beats Consensus ⇒ stronger USD ⇒ bearish
   gold; a miss ⇒ bullish), weighted by impact. The two scores are summed: bullish
   ⇒ buys only, bearish ⇒ sells only, neutral ⇒ no constraint. Everything counts
   only after it is `news_min_age_minutes` (5) old, so it acts *after* the spike,
   never into it, and it never pauses the bot by itself. Backtests ignore this.
   Check the reads with `python -m xauusd_bot.news` and `python -m
   xauusd_bot.econ_calendar`. Toggle with `use_news_bias` / `use_calendar_bias`.
8. **Position size by account balance:** 0.01 lots per $100, increasing 0.01 per
   additional $100 (e.g. $1,000 → 0.10 lots). Below $100 it still trades the
   0.01 minimum. The stop is ATR-based, and the take-profit uses a
   **volatility-adaptive reward:risk** — `1:1` when ATR is elevated (≥
   `high_vol_atr_mult`× its `atr_avg_period` average) and `1:1.5` in normal
   conditions.
9. Holds up to **3 positions** at once; each exit is handled by its own SL/TP.
10. **Account protection floor:** once equity reaches ~70% of the balance it
   started with (it triggers a touch early, ~72%), it closes any open position
   and stops trading for good.

## Requirements

- A **MetaTrader 5 account** (a free demo account from any MT5 broker is fine).
- The **MT5 desktop terminal** installed and logged in. The `MetaTrader5` Python
  package talks to that terminal, so it must be running on the same machine.
  - Native support is Windows. On Linux/macOS it works under Wine, or run the
    terminal + bot in a small Windows VM.
- Python 3.10+.

## Install

```bash
pip install -r requirements.txt
```

## Configure

Credentials are read from environment variables (preferred over hardcoding):

```bash
export MT5_LOGIN=12345678
export MT5_PASSWORD=your_demo_password
export MT5_SERVER="YourBroker-Demo"
# Optional, if MT5 can't auto-detect the terminal:
# export MT5_PATH="C:\\Program Files\\MetaTrader 5\\terminal64.exe"
```

If the terminal is already running and logged in, you can often skip the
login variables entirely — `initialize()` will attach to the open session.

### Find your broker's gold symbol

The symbol name differs between brokers (`XAUUSD`, `XAUUSD.r`, `GOLD`, `XAUUSDm`, …):

```bash
python -m xauusd_bot.bot --list-symbols
```

Set the right one via `MT5_SYMBOL` or by editing `symbol` in `config.py`.

All other tunables (timeframe, indicator periods, risk %, ATR multiples,
spread/daily-loss limits) live in `config.py` with inline comments.

## Run

**On Pepperstone?** Follow `CONNECT_PEPPERSTONE.md` for a step-by-step Windows
demo setup. First verify the connection (read-only, sends no orders):

```bash
python -m xauusd_bot.connect_test
```

When every check is `[OK]`, start the bot:

```bash
python -m xauusd_bot.bot
```

It starts in **dry-run** mode: it logs the trades it *would* place but sends
nothing. When you have validated behaviour on a demo account and understand the
risk, you can set `dry_run = False` in `config.py`.

## Project layout

| File              | Responsibility                                             |
|-------------------|------------------------------------------------------------|
| `config.py`       | All settings, with safe defaults (`dry_run=True`).         |
| `mt5_client.py`   | Everything that talks to MT5: data, orders, positions.     |
| `strategy.py`     | Indicators + the single shared decision rule. No MT5 dep.  |
| `risk.py`         | Position sizing (gold 100 oz contract) + daily-loss guard. |
| `bot.py`          | Live polling loop + CLI + trade journaling.                |
| `data.py`         | CSV loader, synthetic data, MT5 history export.            |
| `backtest.py`     | Event-driven backtester (reuses strategy + risk).          |
| `optimize.py`     | Grid-search parameter tuning ("correct the strategy").     |
| `metrics.py`      | Shared performance metrics for backtest AND live.          |
| `journal.py`      | Append-only CSV of closed live trades.                     |
| `analyze_live.py` | Scores the live journal with the same metrics.             |

## Backtesting

The backtester reuses the exact same signal and risk code the live bot runs, so
results reflect what you'd actually trade. It models spread (as adverse fills on
both entry and exit) plus optional per-lot commission, and resolves stop/target
intrabar (assuming the stop is hit first when a bar spans both — conservative).

```bash
# Quick check on generated data (machinery only — numbers are noise):
python -m xauusd_bot.backtest --synthetic

# On your own history:
python -m xauusd_bot.backtest --csv gold_m15.csv --spread-points 30 --commission 7 \
    --trades-csv trades.csv --plot equity.png
```

Get real history from your broker (MT5 terminal must be running):

```bash
python -m xauusd_bot.data --symbol XAUUSD --timeframe M15 --bars 5000 --out gold_m15.csv
```

A CSV just needs `time, open, high, low, close` columns (names are matched
flexibly), so data from any source works.

## Correcting / tuning the strategy

`optimize.py` runs the backtest across a grid of parameters and ranks them:

```bash
python -m xauusd_bot.optimize --csv gold_m15.csv --min-trades 20 --out-csv ranking.csv
```

Edit the `GRID` dict at the top of `optimize.py` to search a different space.

> Good in-sample numbers do **not** guarantee future profit. Look for *robust*
> regions where many nearby settings all perform decently, not a single peak.
> Always re-test the winner on a later date range it wasn't tuned on, then
> forward-test on demo before trusting it.

## The feedback loop (backtest ↔ live)

When the bot runs live/demo it writes every closed trade to `live_trades.csv`.
Score it with the same metrics and compare to the backtest:

```bash
python -m xauusd_bot.analyze_live --journal live_trades.csv --balance 10000
```

A large gap between live and backtest results usually means real-world friction
the backtest under-modelled (wider spread, slippage, requotes) or a changed
market regime — both are cues to re-run the optimizer and adjust.

## Suggested workflow

1. Export real XAUUSD history (`data.py`).
2. Backtest the defaults; then `optimize.py` to find robust parameters.
3. Validate the chosen parameters on a *different* date range.
4. Put them in `config.py`, run the bot on **demo** with `dry_run=False`.
5. After a while, `analyze_live.py` and compare to the backtest. Adjust, repeat.

## Making it your own

The strategy is isolated in `strategy.py`. To change it, edit
`signal_from_values()` (and the indicators if needed) to return `'buy'`,
`'sell'`, or `None`. Both the live bot and the backtester call it, so they stay
in sync automatically — no risk of testing one thing and trading another.

## Disclaimer

This software is provided for educational purposes only. Trading leveraged
instruments such as XAUUSD carries a high risk of loss. You are solely
responsible for any use of this code and any resulting outcomes. This is not
investment advice; consult a licensed professional for that.
