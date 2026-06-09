"""FXStreet news bias: steer trade DIRECTION from recent USD / gold headlines.

LIVE-ONLY overlay (the backtester has no historical feed). The bot pulls
FXStreet's public RSS, keeps headlines relevant to gold / the US dollar / the
Fed, scores each as bullish or bearish FOR GOLD with a simple, transparent
keyword heuristic, and combines them (recency-weighted) into one bias:

    'bullish' -> only BUYS allowed
    'bearish' -> only SELLS allowed
    'neutral' -> no news constraint (the technical signal decides)

A headline only counts once it is at least `news_min_age_minutes` old, so the
bot waits for the initial post-news spike to settle before acting on it.

This is a ROUGH sentiment heuristic, not real NLP. The word lists below are
meant to be edited — add or remove terms to match how you read the market.
"""
from __future__ import annotations

import logging
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from . import econ_calendar

log = logging.getLogger(__name__)

# A headline must mention at least one of these to be considered at all.
_RELEVANT = (
    "gold", "xau", "dollar", "greenback", "dxy", "fed", "fomc",
    "federal reserve", "powell", "nonfarm", "nfp", "payroll", "cpi",
    "inflation", "interest rate", "rate hike", "rate cut", "treasury yield",
    "yields", "jobs report", "jobless",
)
_GOLD_TERMS = ("gold", "xau/usd", "xauusd", "xau ")

# Direction words applied to a GOLD headline.
_GOLD_UP = ("rallies", "rally", "climbs", "surges", "jumps", "rises", "gains",
            "soars", "advances", "rebounds", "recovers", "spikes", "higher")
_GOLD_DOWN = ("falls", "slides", "tumbles", "drops", "plunges", "crashes",
              "melts down", "collapses", "sinks", "retreats", "slips", "lower",
              "selloff", "sell-off", "pressured")

# A STRONG dollar is bearish for gold; a WEAK dollar is bullish.
_USD_STRONG = ("dollar rallies", "dollar gains", "dollar surges", "dollar climbs",
               "dollar rises", "dollar firms", "dollar strengthens", "stronger dollar",
               "greenback gains", "greenback rallies", "boosts the us dollar",
               "lifts greenback", "lifts the us dollar", "dollar outperforms",
               "dxy above", "dollar bulls", "us dollar rally")
_USD_WEAK = ("dollar falls", "dollar slips", "dollar drops", "dollar weakens",
             "dollar retreats", "weaker dollar", "softer dollar", "dollar bears",
             "greenback falls", "greenback slips", "us dollar bears")

# Fed expectations: hawkish/hikes/higher-for-longer are bearish for gold.
_HAWKISH = ("rate hike", "hike bets", "hawkish", "higher for longer", "restrictive",
            "raise interest rates", "raise rates", "tightening", "hike case")
_DOVISH = ("rate cut", "cut bets", "dovish", "easing", "lower interest rates",
           "rate cuts", "edging toward cuts")

# Strong jobs / upside surprises are bearish for gold (hawkish Fed implication).
_HOT_DATA = ("stronger-than-expected", "stronger than expected", "blowout",
             "upbeat", "beat forecasts", "beat estimates", "robust", "crushed estimates",
             "smashed forecasts", "solid us")
_COOL_DATA = ("weaker-than-expected", "weaker than expected", "disappointing",
              "missed forecasts", "soft", "cooling labor", "downbeat")


def _headline_score(text: str) -> float:
    """Signed sentiment FOR GOLD: > 0 bullish, < 0 bearish, 0 = no read."""
    t = text.lower()
    if not any(k in t for k in _RELEVANT):
        return 0.0
    score = 0.0
    if any(k in t for k in _GOLD_TERMS):
        if any(w in t for w in _GOLD_UP):
            score += 1.0
        if any(w in t for w in _GOLD_DOWN):
            score -= 1.0
    if any(w in t for w in _USD_STRONG):
        score -= 0.7
    if any(w in t for w in _USD_WEAK):
        score += 0.7
    if any(w in t for w in _HAWKISH):
        score -= 0.6
    if any(w in t for w in _DOVISH):
        score += 0.6
    # Hot/cool macro data only counts near jobs/payroll/inflation context.
    if any(k in t for k in ("nfp", "payroll", "jobs", "cpi", "inflation")):
        if any(w in t for w in _HOT_DATA):
            score -= 0.5
        if any(w in t for w in _COOL_DATA):
            score += 0.5
    return score


def _parse_date(s: str | None) -> datetime | None:
    """Parse the various pubDate formats RSS feeds use, returning a UTC datetime."""
    if not s:
        return None
    s = s.strip().replace(" Z", " +0000").replace(" GMT", " +0000").replace(" UTC", " +0000")
    fmts = (
        "%a, %d %b %Y %H:%M:%S %z",   # RFC822 (Google News, FXStreet)
        "%Y-%m-%d %H:%M:%S",          # Investing.com (assume UTC)
        "%Y-%m-%dT%H:%M:%S%z",        # ISO 8601
    )
    for fmt in fmts:
        try:
            dt = datetime.strptime(s, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def fetch_headlines(url: str, timeout: int = 8):
    """Return a list of (pubdate_utc, title, description). Raises on network error."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (xauusd-bot)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    root = ET.fromstring(raw)
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        desc = (it.findtext("description") or "").strip()
        pub = _parse_date(it.findtext("pubDate"))
        if pub is not None:
            items.append((pub, title, desc))
    return items


def fetch_first(urls, timeout: int = 8):
    """Try each feed URL in order; return (url, items) for the first that yields items.

    Returns (None, []) if every feed fails or is empty. Network/parse errors on
    one feed are logged and the next is tried, so a regional block on one source
    doesn't disable the news bias.
    """
    if isinstance(urls, str):
        urls = (urls,)
    last_err = None
    for url in urls:
        try:
            items = fetch_headlines(url, timeout)
            if items:
                return url, items
        except Exception as exc:
            last_err = exc
            log.debug("News feed failed (%s): %s", url, exc)
    if last_err is not None:
        log.warning("All news feeds failed; last error: %s", last_err)
    return None, []


def gold_bias(items, now=None, min_age_minutes=5, lookback_hours=6.0, threshold=0.5):
    """Combine recent relevant headlines into ('bullish'|'bearish'|'neutral', score, latest_title)."""
    now = now or datetime.now(timezone.utc)
    total = 0.0
    latest_title = None
    latest_age = None
    for pub, title, desc in items:
        age_min = (now - pub).total_seconds() / 60.0
        if age_min < min_age_minutes:          # wait for the spike to settle
            continue
        if age_min > lookback_hours * 60:       # too old to matter
            continue
        s = _headline_score(f"{title} {desc}")
        if s == 0.0:
            continue
        weight = max(0.0, 1.0 - age_min / (lookback_hours * 60))
        total += s * weight
        if latest_age is None or age_min < latest_age:
            latest_age, latest_title = age_min, title
    if total >= threshold:
        bias = "bullish"
    elif total <= -threshold:
        bias = "bearish"
    else:
        bias = "neutral"
    return bias, total, latest_title


class NewsBiasFeed:
    """Caches the RSS pull so the bot refetches at most every `news_refresh_seconds`."""

    def __init__(self, cfg):
        self.cfg = cfg
        self._items = []
        self._last_fetch = 0.0
        self.source = None      # which feed URL last succeeded
        self._calendar = (econ_calendar.CalendarFeed(cfg)
                          if getattr(cfg, "use_calendar_bias", False) else None)

    def current(self):
        """Return ('bullish'|'bearish'|'neutral', combined_score, latest_label).

        Combines the RSS headline score with the economic-calendar surprise score
        (both on the gold scale), then thresholds the SUM into one direction.
        """
        now = time.time()
        if now - self._last_fetch >= self.cfg.news_refresh_seconds:
            url, items = fetch_first(self.cfg.news_rss_urls, self.cfg.news_fetch_timeout)
            if items:                       # only replace on success; else keep last
                self._items, self.source = items, url
            self._last_fetch = now

        _, rss_score, rss_latest = gold_bias(
            self._items,
            min_age_minutes=self.cfg.news_min_age_minutes,
            lookback_hours=self.cfg.news_lookback_hours,
            threshold=self.cfg.news_threshold,
        )
        cal_score, cal_latest = (0.0, None)
        if self._calendar is not None:
            cal_score, cal_latest = self._calendar.score()

        total = rss_score + cal_score
        thr = self.cfg.news_threshold
        bias = "bullish" if total >= thr else "bearish" if total <= -thr else "neutral"
        # Surface whichever source contributed the stronger pull.
        latest = cal_latest if (cal_latest and abs(cal_score) >= abs(rss_score)) else (rss_latest or cal_latest)
        return bias, total, latest


def main() -> None:
    """`python -m xauusd_bot.news` — print the current gold news bias and headlines."""
    from .config import Config
    cfg = Config()
    print("Trying news feeds in order...")
    url, items = fetch_first(cfg.news_rss_urls, cfg.news_fetch_timeout)
    if not items:
        print("[FAIL] No news feed was reachable. Check your connection, or the")
        print("       feeds may be blocked in your region. The bot keeps trading")
        print("       on technicals with a neutral news bias regardless.")
        return
    print(f"[OK] Using feed: {url}")
    bias, score, latest = gold_bias(
        items, min_age_minutes=cfg.news_min_age_minutes,
        lookback_hours=cfg.news_lookback_hours, threshold=cfg.news_threshold,
    )
    print(f"\nGOLD NEWS BIAS = {bias.upper()}  (score {score:+.2f})")
    print("  bullish -> only BUYS | bearish -> only SELLS | neutral -> technicals decide\n")
    print("Recent relevant headlines (scored):")
    shown = 0
    for pub, title, desc in items:
        s = _headline_score(f"{title} {desc}")
        if s != 0.0:
            print(f"  {s:+.2f}  {pub:%Y-%m-%d %H:%M}  {title[:70]}")
            shown += 1
        if shown >= 12:
            break


if __name__ == "__main__":
    main()
