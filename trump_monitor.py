#!/usr/bin/env python3
"""
Trump Stock Monitor — GitHub Actions version
Runs every 15 minutes via GitHub Actions (free, 24/7, no server needed).
Sends Telegram alerts when Trump mentions stocks, ETFs, or market terms.
"""

import feedparser
import json
import os
import re
import requests
from datetime import datetime
from pathlib import Path

# ── Credentials (from GitHub Actions secrets / env vars) ──────────────────────
TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID", "7043700069"))

if not TOKEN:
    raise SystemExit("ERROR: TELEGRAM_BOT_TOKEN env var not set. Add it as a GitHub secret.")

# ── Paths ──────────────────────────────────────────────────────────────────────
HISTORY_FILE = Path("trump_monitor_history.json")

# ── Sources ────────────────────────────────────────────────────────────────────
RSS_FEEDS = [
    # Trump's own accounts — flag every post
    ("Truth Social",             "https://truthsocial.com/@realDonaldTrump.rss"),
    ("Rumble – Trump",           "https://rumble.com/c/DonaldTrump.rss"),
    ("YouTube – Trump",          "https://www.youtube.com/feeds/videos.xml?channel_id=UCRzQEMwgKFe1nHNAltoHJPw"),
    ("Gab – Trump",              "https://gab.com/realDonaldTrump.rss"),
    ("Gettr – Trump",            "https://gettr.com/user/realdonaldtrump.rss"),
    ("X via Nitter",             "https://nitter.poast.org/realDonaldTrump/rss"),
    ("X via Nitter 2",           "https://nitter.privacydev.net/realDonaldTrump/rss"),
    # White House
    ("White House",              "https://www.whitehouse.gov/briefings-statements/feed/"),
    # News
    ("Reuters Top",              "https://feeds.reuters.com/reuters/topNews"),
    ("Reuters Politics",         "https://feeds.reuters.com/Reuters/PoliticsNews"),
    ("AP Top",                   "https://feeds.apnews.com/rss/apf-topnews"),
    ("AP Politics",              "https://feeds.apnews.com/rss/apf-politics"),
    ("CNN Politics",             "https://rss.cnn.com/rss/cnn_allpolitics.rss"),
    ("Fox News Politics",        "https://feeds.foxnews.com/foxnews/politics"),
    ("Fox Business",             "https://feeds.foxbusiness.com/foxbusiness/latest"),
    ("The Hill",                 "https://thehill.com/feed/"),
    ("Politico",                 "https://www.politico.com/rss/politicopicks.xml"),
    ("NBC Politics",             "https://feeds.nbcnews.com/nbcnews/public/politics"),
    ("NY Post",                  "https://nypost.com/feed/"),
    ("Breitbart",                "https://feeds.feedburner.com/breitbart"),
    ("Daily Caller",             "https://dailycaller.com/feed/"),
    ("RealClearPolitics",        "https://www.realclearpolitics.com/index.xml"),
    # Finance
    ("CNBC",                     "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("MarketWatch",              "https://feeds.marketwatch.com/marketwatch/realtimeheadlines/"),
    ("Yahoo Finance",            "https://finance.yahoo.com/news/rssindex"),
    ("Bloomberg Markets",        "https://feeds.bloomberg.com/markets/news.rss"),
    ("Bloomberg Politics",       "https://feeds.bloomberg.com/politics/news.rss"),
    ("Seeking Alpha",            "https://seekingalpha.com/feed.xml"),
    ("Investors.com",            "https://www.investors.com/feed/"),
    ("Wall Street Journal",      "https://feeds.a.dj.com/rss/RSSWorldNews.xml"),
]

TRUMP_SOCIAL = {
    "Truth Social", "Rumble – Trump", "YouTube – Trump",
    "Gab – Trump", "Gettr – Trump", "X via Nitter", "X via Nitter 2",
}

# ── Detection patterns ─────────────────────────────────────────────────────────
TRUMP_RE = re.compile(
    r'\b(trump|donald trump|president trump|trump\'s|@realdonaldtrump|'
    r'45th president|47th president)\b', re.IGNORECASE
)

TICKERS = {
    "AAPL","MSFT","NVDA","AMZN","GOOGL","GOOG","META","TSLA","AVGO",
    "JPM","LLY","V","MA","UNH","XOM","WMT","PG","HD","COST","JNJ",
    "MRK","CVX","ABBV","BAC","KO","PEP","NFLX","AMD","ADBE","CRM",
    "ORCL","INTC","QCOM","GS","MS","BLK","AXP","CAT","DE","BA","F","GM",
    "UBER","COIN","MSTR","MARA","RIOT","GBTC","IBIT","FBTC",
    "SPY","QQQ","IWM","DIA","VTI","GLD","SLV","TLT","USO","ARKK",
    "SOXX","SMH","XLF","XLK","XLE","TQQQ","SQQQ","SPXL","SPXU",
    "EEM","AGG","BND","JEPI","JEPQ","SCHD",
}

COMPANY_NAMES = {
    "apple","microsoft","nvidia","amazon","google","alphabet","meta","facebook",
    "tesla","berkshire","jpmorgan","jp morgan","eli lilly","visa","mastercard",
    "unitedhealth","exxon","exxonmobil","walmart","procter","home depot",
    "costco","johnson & johnson","merck","chevron","abbvie","bank of america",
    "coca-cola","pepsi","pepsico","netflix","advanced micro devices","adobe",
    "salesforce","oracle","intel","qualcomm","goldman sachs","goldman",
    "morgan stanley","blackrock","american express","caterpillar","deere",
    "john deere","boeing","ford","general motors","uber","coinbase",
    "microstrategy","marathon digital","riot platforms","rivian","lucid",
}

# Only tradeable assets — no broad macro/policy terms
TRADEABLE_ASSETS = re.compile(
    r'\b(bitcoin|btc|ethereum|eth|dogecoin|doge|solana|sol|xrp|ripple|'
    r'crude oil|natural gas|gold|silver|platinum|'
    r'spy|qqq|iwm|dia|gld|slv|tlt|arkk|soxx|smh|tqqq|sqqq|'
    r's&p\s*500 (etf|fund)|nasdaq (etf|fund)|dow (etf|fund))\b',
    re.IGNORECASE
)

TICKER_RE = re.compile(
    r'\b(?:' + '|'.join(re.escape(t) for t in sorted(TICKERS, key=len, reverse=True)) + r')\b'
)

COMPANY_RE = re.compile(
    r'\b(?:' + '|'.join(re.escape(c) for c in sorted(COMPANY_NAMES, key=len, reverse=True)) + r')\b',
    re.IGNORECASE
)

# ── Helpers ────────────────────────────────────────────────────────────────────

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def load_history():
    if HISTORY_FILE.exists():
        try:
            return set(json.loads(HISTORY_FILE.read_text())["seen"])
        except Exception:
            pass
    return set()


def save_history(seen):
    HISTORY_FILE.write_text(json.dumps({
        "seen": list(seen)[-10_000:],
        "last_run": datetime.utcnow().isoformat(),
    }, indent=2))


def fetch_feed(name, url):
    try:
        f = feedparser.parse(url, request_headers={"User-Agent": "TrumpStockMonitor/2.0"})
        return [{
            "source": name,
            "title":   getattr(e, "title",   ""),
            "summary": getattr(e, "summary", ""),
            "link":    getattr(e, "link",    ""),
            "pub":     getattr(e, "published",""),
        } for e in f.entries]
    except Exception as e:
        log(f"  SKIP {name}: {e}")
        return []


def has_financial_mention(text):
    # Must name a specific stock, company, ETF, or tradeable asset — no generic macro terms
    if TICKER_RE.search(text):
        return True
    if COMPANY_RE.search(text):
        return True
    if TRADEABLE_ASSETS.search(text):
        return True
    return False


def send_telegram(alert):
    title   = alert["title"][:200]
    source  = alert["source"]
    snippet = alert["summary"][:250].strip()
    link    = alert.get("link", "")

    lines = [
        "🚨 <b>Trump Stock Alert</b>",
        f"📌 <b>{title}</b>",
        f"🗞 <i>{source}</i>",
    ]
    if snippet:
        lines.append(f"\n{snippet}{'…' if len(alert['summary']) > 250 else ''}")
    if link:
        lines.append(f"\n🔗 <a href=\"{link}\">Read more</a>")

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={
                "chat_id":    CHAT_ID,
                "text":       "\n".join(lines),
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=10,
        )
        if r.ok:
            log(f"  ✅ Sent alert: {title[:60]}")
            return True
        else:
            log(f"  ❌ Telegram error: {r.text[:100]}")
    except Exception as e:
        log(f"  ❌ Telegram exception: {e}")
    return False


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    log(f"=== Trump Stock Monitor — {len(RSS_FEEDS)} sources ===")
    seen    = load_history()
    alerts  = 0
    scanned = 0

    for name, url in RSS_FEEDS:
        articles = fetch_feed(name, url)
        log(f"  {name}: {len(articles)} articles")

        for art in articles:
            uid  = art["link"] or art["title"]
            text = f"{art['title']} {art['summary']}"
            seen.add(uid)
            scanned += 1

            # Must mention Trump (or be from his account) AND mention finance
            is_trump_source = name in TRUMP_SOCIAL
            mentions_trump  = is_trump_source or bool(TRUMP_RE.search(text))
            if not mentions_trump:
                continue
            if not has_financial_mention(text):
                continue

            # Skip already-alerted articles
            if uid in seen - {uid}:   # uid just added above; check prior history
                continue

            if send_telegram(art):
                alerts += 1

    save_history(seen)
    log(f"=== Done — {scanned} articles scanned, {alerts} alerts sent ===")


if __name__ == "__main__":
    main()
