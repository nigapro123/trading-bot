"""FXStreet economic-calendar bias (LIVE only).

Turns scheduled USD economic releases into a gold direction — the same idea as
the headline news bias, but from hard data points (Actual vs Consensus):

    beat  (economy stronger than expected) -> USD up   -> gold BEARISH
    miss  (weaker than expected)           -> USD down -> gold BULLISH

Events are weighted by impact (volatility) and recency, count only once they are
`news_min_age_minutes` old (act after the print, not into it), and never pause
the bot. The fxstreet.com/economic-calendar PAGE is JavaScript-rendered, so this
uses its JSON data API instead. That API is geo-blocked in the same regions as
the rest of FXStreet, so it needs your VPN; if it is unreachable the calendar
simply contributes nothing (neutral) and the bot is unaffected.

Verify what it parses on your VPN with:  python -m xauusd_bot.econ_calendar
"""
from __future__ import annotations

import json
import logging
import time
import urllib.request
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

# Impact -> weight. High-impact prints (NFP, CPI, FOMC) dominate the score.
_VOL_WEIGHT = {"HIGH": 1.0, "MEDIUM": 0.6, "LOW": 0.3, "NONE": 0.1, None: 0.3}

# Indicators where a HIGHER actual is USD-NEGATIVE ("lower is better" for USD).
# Everything else assumes higher actual = stronger economy = USD-positive.
_LOWER_IS_USD_POSITIVE = (
    "unemployment rate", "jobless claims", "initial jobless", "continuing claims",
    "unemployment change", "trade balance", "trade deficit", "budget deficit",
    "crude oil inventories",
)


def _to_float(x):
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace(",", "").replace("%", "")
    if not s or s in ("-", "n/a", "N/A"):
        return None
    mult = 1.0
    if s[-1] in "KkMmBbTt":
        mult = {"k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12}[s[-1].lower()]
        s = s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return None


def _parse_dt(s):
    if not s:
        return None
    s = str(s).strip().replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _norm(ev: dict) -> dict:
    """Normalise one calendar event across possible field-name variants."""
    g = ev.get
    consensus = g("consensus")
    if consensus is None:
        consensus = g("forecast")
    return {
        "dt": _parse_dt(g("dateUtc") or g("date") or g("dateTime")),
        "currency": str(g("currencyCode") or g("currency") or "").upper(),
        "country": str(g("countryCode") or g("country") or "").upper(),
        "name": g("name") or g("title") or g("event") or "",
        "vol": (str(g("volatility") or g("impact") or "").upper() or None),
        "actual": _to_float(g("actual")),
        "consensus": _to_float(consensus),
        "previous": _to_float(g("previous")),
        "better": g("isBetterThanExpected"),
    }


def _event_gold_dir(e: dict) -> int:
    """+1 bullish gold, -1 bearish gold, 0 no read, from the data surprise."""
    a, c = e["actual"], e["consensus"]
    if a is None or c is None:
        return 0
    if e["better"] is not None:                 # FXStreet pre-computes beat/miss
        usd = 1 if e["better"] else -1
    else:
        if a == c:
            return 0
        higher_is_usd_pos = not any(k in e["name"].lower() for k in _LOWER_IS_USD_POSITIVE)
        usd = (1 if a > c else -1) * (1 if higher_is_usd_pos else -1)
    return -usd                                 # stronger USD -> weaker gold


def fetch_calendar(url_template: str, start: datetime, end: datetime, timeout: int = 8):
    """Fetch the calendar JSON for [start, end]. Raises on network/parse error."""
    url = (url_template
           .replace("{start}", start.strftime("%Y-%m-%dT%H:%M:%SZ"))
           .replace("{end}", end.strftime("%Y-%m-%dT%H:%M:%SZ")))
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (xauusd-bot)",
        "Accept": "application/json",
        "Referer": "https://www.fxstreet.com/economic-calendar",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    if isinstance(data, dict):
        for key in ("data", "events", "items", "result"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    return data if isinstance(data, list) else []


def calendar_score(events, now=None, min_age_minutes=5, lookback_hours=6.0,
                   currencies=("USD",)):
    """Signed gold score from recent released events + a label for the top one."""
    now = now or datetime.now(timezone.utc)
    total = 0.0
    latest = None
    latest_age = None
    for raw in events:
        e = _norm(raw)
        if e["dt"] is None:
            continue
        if currencies and e["currency"] not in currencies and e["country"] not in ("US", "USA"):
            continue
        age = (now - e["dt"]).total_seconds() / 60.0
        if age < min_age_minutes:               # upcoming, or too fresh -> wait
            continue
        if age > lookback_hours * 60:           # already priced in
            continue
        d = _event_gold_dir(e)
        if d == 0:
            continue
        weight = _VOL_WEIGHT.get(e["vol"], 0.3) * max(0.0, 1.0 - age / (lookback_hours * 60))
        total += d * weight
        if latest_age is None or age < latest_age:
            latest_age = age
            latest = f'{e["name"]} (actual {e["actual"]} vs cons {e["consensus"]})'
    return total, latest


class CalendarFeed:
    """Caches the calendar pull so the bot refetches at most every news_refresh_seconds."""

    def __init__(self, cfg):
        self.cfg = cfg
        self._events = []
        self._last = 0.0

    def score(self):
        now = time.time()
        if now - self._last >= self.cfg.news_refresh_seconds:
            try:
                start = datetime.now(timezone.utc) - timedelta(hours=self.cfg.news_lookback_hours + 2)
                end = datetime.now(timezone.utc) + timedelta(hours=12)
                self._events = fetch_calendar(
                    self.cfg.calendar_api_url, start, end, self.cfg.news_fetch_timeout
                )
            except Exception as exc:
                log.debug("Calendar fetch failed: %s", exc)
            self._last = now
        return calendar_score(
            self._events,
            min_age_minutes=self.cfg.news_min_age_minutes,
            lookback_hours=self.cfg.news_lookback_hours,
            currencies=tuple(self.cfg.calendar_currencies),
        )


def main() -> None:
    """`python -m xauusd_bot.econ_calendar` — dump parsed USD events (run on VPN)."""
    from .config import Config
    cfg = Config()
    start = datetime.now(timezone.utc) - timedelta(hours=24)
    end = datetime.now(timezone.utc) + timedelta(hours=24)
    print("Fetching FXStreet calendar API (needs VPN where FXStreet is blocked)...")
    try:
        events = fetch_calendar(cfg.calendar_api_url, start, end, cfg.news_fetch_timeout)
    except Exception as exc:
        print(f"[FAIL] Could not fetch the calendar API: {exc}")
        print("       If this is a 403/parse error, paste it here and I'll fix the URL/fields.")
        return
    print(f"[OK] {len(events)} raw events fetched. USD events parsed:\n")
    shown = 0
    for raw in events:
        e = _norm(raw)
        if e["currency"] != "USD" and e["country"] not in ("US", "USA"):
            continue
        d = _event_gold_dir(e)
        tag = {1: "bullish", -1: "bearish", 0: "—"}[d]
        when = e["dt"].strftime("%Y-%m-%d %H:%M") if e["dt"] else "?"
        print(f"  {when}  [{e['vol'] or '?':6}]  {e['name'][:42]:42}  "
              f"A={e['actual']} C={e['consensus']}  -> gold {tag}")
        shown += 1
    if not shown:
        print("  (no USD events parsed — paste one raw event's JSON and I'll map the fields)")
    score, latest = calendar_score(events, currencies=tuple(cfg.calendar_currencies))
    print(f"\nCalendar gold score = {score:+.2f}  (latest: {latest})")


if __name__ == "__main__":
    main()
