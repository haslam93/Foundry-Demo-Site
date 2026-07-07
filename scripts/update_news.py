"""Weekly updater: pulls Microsoft Foundry blog posts and Agent Framework releases,
summarizes them with GitHub Models, and merges them into news.json for the site."""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests

ROOT = Path(__file__).resolve().parent.parent
NEWS_FILE = ROOT / "news.json"
LOOKBACK_DAYS = 10  # slight overlap with weekly cadence so nothing is missed
MAX_ITEMS = 30

FEEDS = [
    ("foundry", "https://devblogs.microsoft.com/foundry/feed/"),
    ("agent-framework", "https://github.com/microsoft/agent-framework/releases.atom"),
]

GH_TOKEN = os.environ.get("GITHUB_TOKEN", "")
MODELS_URL = "https://models.github.ai/inference/chat/completions"
MODEL = "openai/gpt-4o-mini"


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "").replace("&nbsp;", " ").strip()


def summarize(title: str, body: str, source: str) -> str:
    """Summarize with GitHub Models; fall back to a trimmed excerpt on any failure."""
    fallback = strip_html(body)[:280]
    if not GH_TOKEN:
        return fallback
    try:
        resp = requests.post(
            MODELS_URL,
            headers={
                "Authorization": f"Bearer {GH_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "model": MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You write one-sentence to two-sentence summaries (max 60 words) of "
                            "Microsoft Foundry and Microsoft Agent Framework announcements for a "
                            "developer audience. Be concrete about what shipped and why it matters. "
                            "No hype words, no emojis."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Source: {source}\nTitle: {title}\n\nContent:\n{strip_html(body)[:4000]}",
                    },
                ],
                "temperature": 0.3,
                "max_tokens": 120,
            },
            timeout=60,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"].strip()
        return text or fallback
    except Exception as exc:  # noqa: BLE001 - never fail the run on summarization
        print(f"  summarize failed ({exc}); using excerpt", file=sys.stderr)
        return fallback


def guess_tags(title: str, source: str) -> list[str]:
    t = title.lower()
    tags = []
    if source == "agent-framework":
        tags.append("SDK")
    if any(k in t for k in ("model", "claude", "gpt", "phi", "llama", "grok")):
        tags.append("Models")
    if "agent" in t:
        tags.append("Agents")
    if any(k in t for k in ("eval", "red team", "observab", "trace", "monitor", "governan", "safety")):
        tags.append("Day 2 Ops")
    if any(k in t for k in ("ga", "generally available", "general availability")):
        tags.append("GA")
    if "preview" in t:
        tags.append("Preview")
    return tags[:3] or ["Update"]


def main() -> None:
    news = json.loads(NEWS_FILE.read_text(encoding="utf-8"))
    existing_urls = {item.get("url") for item in news.get("items", [])}
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    added = 0

    for source, feed_url in FEEDS:
        print(f"Checking {source}: {feed_url}")
        parsed = feedparser.parse(feed_url)
        for entry in parsed.entries[:15]:
            url = entry.get("link", "")
            if not url or url in existing_urls:
                continue
            published = entry.get("published_parsed") or entry.get("updated_parsed")
            if published:
                when = datetime(*published[:6], tzinfo=timezone.utc)
                if when < cutoff:
                    continue
                date_str = when.strftime("%Y-%m-%d")
            else:
                date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            title = strip_html(entry.get("title", "Untitled"))
            body = entry.get("summary", "") or (entry.get("content", [{}])[0].get("value", "") if entry.get("content") else "")
            print(f"  new: {title}")
            news["items"].insert(
                0,
                {
                    "date": date_str,
                    "title": title,
                    "summary": summarize(title, body, source),
                    "url": url,
                    "source": source,
                    "tags": guess_tags(title, source),
                },
            )
            existing_urls.add(url)
            added += 1

    if added:
        news["items"].sort(key=lambda i: i.get("date", ""), reverse=True)
        news["items"] = news["items"][:MAX_ITEMS]
        news["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        NEWS_FILE.write_text(json.dumps(news, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Added {added} item(s); news.json updated.")
    else:
        print("No new items this week.")


if __name__ == "__main__":
    main()
