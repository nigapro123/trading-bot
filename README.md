# XAUUSD Trading Bot (MetaTrader 5)

A small, readable trading bot for the **gold / USD (XAUUSD)** pair. It connects to
a MetaTrader 5 broker account and, on closed bars, runs a configurable strategy —
**RSI mean-reversion by default**, with optional ADX-driven regime switching and a
top-down trend-following mode. It sizes positions from the account balance and
manages exits with ATR-based stop loss / take profit.

> ⚠️ **This is an educational starting template, not a profitable strategy.**
> Automated trading can lose money quickly. Run it on a **demo account** with
> `dry_run = True` and watch it for a long time before considering anything else.
> Nothing here is financial advice.

## What it does

> **Current setup: pure RSI mean-reversion on M1.** With the `config.py` defaults
> (`adaptive_regime = False`, `mean_reversion = True`) the bot fades RSI extremes
> on the execution chart:
>
> - **BUY** when RSI(7) crosses down to ≤ `rsi_buy_level` (**30**) and price is at
>   or below VWAP.
> - **SELL** when RSI(7) crosses up to ≥ `rsi_sell_level` (**70**) and price is at
>   or above VWAP.
> - Each trade exits on its **ATR take-profit (1:1) or ATR stop** — winners run to
>   the target. The quick RSI-zone exit is **off** by default; set
>   `meanrev_rsi_exit = True` to re-enable it (closes the moment RSI leaves the
>   zone — quicker scalps, but cuts winners short).
> - Trend, news, calendar and S/R filters are **ignored** in this mode; only VWAP
>   confirms. Up to **3 positions** at once.

These always apply, in every mode:

1. Connects to your MT5 terminal/account and acts on **closed** bars.
2. **Position size by balance:** `lots_per_step` (0.01) lots per `balance_per_step`
   ($1,000) of balance — e.g. a ~$50k account trades ~**0.50 lots**, floored at
   the 0.01 broker minimum. Lower `balance_per_step` for larger size.
3. **Reward:risk** is a flat **1:1** (`reward_risk` = `reward_risk_high_vol` = 1.0).
   It can adapt to volatility — set `reward_risk_high_vol` below `reward_risk`
   (e.g. 1.0 vs 1.5) to use a wider target in calm markets and tighten in volatile
   ones (ATR ≥ `high_vol_atr_mult` × its `atr_avg_period` average).
4. **Account-protection floor:** once equity falls to ~70% of the balance it
   started with (triggers a touch early, ~72%), it closes any open position and
   stops trading for good.

### Optional modes

Two other styles are built in and toggled in `config.py`:

- `adaptive_regime = True` — auto-switch by trend STRENGTH: **H1 ADX ≥
  `trend_strength_adx` (25)** → trend-following, otherwise RSI mean-reversion.
- `mean_reversion = False` (with `adaptive_regime = False`) — always **top-down
  trend-following**.

The trend-following side adds these filters (all ignored by the mean-reversion
default above):

- **Top-down, two timeframes:** trend + key support/resistance from H1, execution
  on M1/M5. EMA9 vs EMA20 on H1 sets the allowed direction.
- **Triggers:** an EMA(9/20) crossover OR a trend-aligned RSI(7) extreme.
- **VWAP filter** and **S/R gate** (`use_vwap_filter` / `use_sr_filter`).
- **News bias (live only):** FXStreet RSS (Google News as a no-VPN fallback) plus
  the FXStreet economic-calendar surprise (Actual vs Consensus, impact-weighted),
  summed to bias direction — bullish ⇒ buys only, bearish ⇒ sells only. Counts
  only after `news_min_age_minutes` (5). Check with `python -m xauusd_bot.news`
  and `python -m xauusd_bot.econ_calendar`; toggle `use_news_bias` /
  `use_calendar_bias`.

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

All other tunables (timeframes, indicator periods, RSI levels, balance-based
sizing, ATR multiples, spread limit, the 70% equity floor, news/calendar) live in
`config.py` with inline comments.

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
| `strategy.py`     | Indicators (EMA/RSI/ATR/VWAP/ADX) + the shared decision rules. No MT5 dep. |
| `risk.py`         | Balance-based position sizing (gold 100 oz) + 70% equity floor. |
| `bot.py`          | Live polling loop + CLI + trade journaling.                |
| `data.py`         | CSV loader, synthetic data, MT5 history export.            |
| `backtest.py`     | Event-driven backtester (reuses strategy + risk).          |
| `optimize.py`     | Grid-search parameter tuning ("correct the strategy").     |
| `metrics.py`      | Shared performance metrics for backtest AND live.          |
| `journal.py`      | Append-only CSV of closed live trades.                     |
| `analyze_live.py` | Scores the live journal with the same metrics.             |
| `news.py`         | FXStreet/Google-News RSS bias (live only).                 |
| `econ_calendar.py`| FXStreet economic-calendar surprise bias (live only).      |
| `connect_test.py` | Read-only MT5 connection diagnostic.                       |

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
