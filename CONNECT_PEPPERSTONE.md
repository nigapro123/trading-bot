# Connecting the bot to Pepperstone MT5 (demo) — Windows quickstart

This gets your bot talking to the **Pepperstone MetaTrader 5 desktop terminal**
you just installed, on a **demo account**, and confirms the live data feed —
without sending a single order.

> The `MetaTrader5` Python package controls the terminal that is **running on the
> same Windows PC**. So the terminal must be open and logged in whenever the bot
> (or the connection test) runs.

---

## 1. Open a Pepperstone MT5 *demo* account

If you don't have demo login details yet:

1. Go to Pepperstone's site and create a **demo / practice** MT5 account
   (free, virtual money). You'll receive a **login number**, a **password**, and
   a **server name** (e.g. something like `Pepperstone-Demo` or
   `Pepperstone-Edge-Demo` — use exactly what they give you).
2. Open the MT5 desktop terminal → **File > Login to Trade Account** → enter the
   login, password, and pick that server. Bottom-right should show a live
   connection (a moving price + signal bars), not "No connection".

## 2. Enable Algo Trading in the terminal

The bot can read data without this, but it needs it to place orders later:

- **Tools > Options > Expert Advisors** → tick **"Allow Algo Trading"**.
- On the toolbar, click the **Algo Trading** button so it turns **green**.

## 3. Confirm the gold symbol is visible

Pepperstone's gold symbol on MT5 is **`XAUUSD`** (already the default in
`config.py`). Make sure it shows in **Market Watch**:

- If you don't see it: right-click in Market Watch → **Symbols** /
  **Show All**, find XAUUSD, and add it. (Pepperstone also offers `XAUEUR`,
  `XAUCHF`, `XAUCNH` — stick with `XAUUSD` unless you have a reason not to.)

## 4. Install Python + the dependencies

Install **Python 3.10+** from python.org (tick *"Add Python to PATH"* during
setup). Then, in a terminal (PowerShell or Command Prompt) **in this folder**:

```powershell
python -m pip install -r requirements.txt
```

That installs `MetaTrader5`, `pandas`, and `numpy`.

## 5. Run the connection test

With the MT5 terminal still **open and logged in**, from this folder run:

```powershell
python -m xauusd_bot.connect_test
```

It prints an `[OK]` / `[FAIL]` line for each check — terminal connected,
algo-trading on, demo account, gold symbol found, bars pulled, live tick — and a
final verdict. **Re-run it until every line is `[OK]`.**

### If it can't connect

The test attaches to the already-open terminal by default. If that fails, give
it your credentials via environment variables (PowerShell, current window only):

```powershell
$env:MT5_LOGIN    = "12345678"            # your demo login number
$env:MT5_PASSWORD = "your_demo_password"
$env:MT5_SERVER   = "Pepperstone-Demo"    # the EXACT server name Pepperstone gave you
python -m xauusd_bot.connect_test
```

Other common fixes:
- **"No module named MetaTrader5"** → you're not in the right Python / didn't run
  step 4, or you're not on Windows. The package is Windows-native.
- **Symbol not found** → do step 3, or set `$env:MT5_SYMBOL` to the name the test
  lists under "Gold-like symbols offered".
- **Terminal not connected** → log the terminal back in (step 1); a greyed-out
  connection icon means no feed.

## 6. Start the bot in dry-run (no orders)

Once the test is all green:

```powershell
python -m xauusd_bot.bot
```

It starts in **DRY-RUN** (`dry_run = True` in `config.py`): it evaluates the
strategy on each newly closed bar and **logs the trades it *would* place, but
sends nothing**. Leave it running and watch the log for a while.

## 7. (Later) let it actually trade the demo

Only after you've watched dry-run behave sensibly and you understand the risk:

1. Optionally export real history and backtest/tune first (see `README.md` →
   *Backtesting* and *Correcting / tuning the strategy*).
2. Set `dry_run = False` in `config.py`.
3. Run `python -m xauusd_bot.bot` again — now it sends real orders **to the demo
   account**. Every closed trade is written to `live_trades.csv`; score it with
   `python -m xauusd_bot.analyze_live --journal live_trades.csv`.

Keep it on **demo** until the live results match the backtest over many weeks.

---

### One-glance command list

```powershell
python -m pip install -r requirements.txt   # once
python -m xauusd_bot.connect_test           # verify connection (read-only)
python -m xauusd_bot.bot                     # run the bot (dry-run by default)
```

> ⚠️ Automated trading can lose money fast. This is an educational template, not
> a profitable strategy, and nothing here is financial advice. Demo first.
