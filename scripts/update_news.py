"""Refresh the site's Microsoft Foundry update feed from official sources."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parent.parent
NEWS_FILE = ROOT / "news.json"
LOOKBACK_DAYS = 21
MAX_ITEMS = 60
REQUEST_TIMEOUT = (10, 45)
USER_AGENT = "Foundry-Demo-Site/2.0 (+https://github.com/haslam93/Foundry-Demo-Site)"

GH_TOKEN = os.environ.get("GITHUB_TOKEN", "")
MODELS_URL = "https://models.github.ai/inference/chat/completions"
MODEL = os.environ.get("GITHUB_MODELS_MODEL", "openai/gpt-4o-mini")

SOURCE_DEFINITIONS = [
    {
        "id": "azure-updates",
        "label": "Azure Updates",
        "kind": "Release status",
        "url": "https://azure.microsoft.com/updates/?products=ai-foundry",
        "feed_url": "https://www.microsoft.com/releasecommunications/api/v2/Azure/rss",
        "authority": "Canonical availability",
    },
    {
        "id": "foundry-docs",
        "label": "Microsoft Learn",
        "kind": "Documentation",
        "url": "https://learn.microsoft.com/azure/foundry/whats-new-foundry",
        "authority": "Canonical product guidance",
    },
    {
        "id": "foundry-blog",
        "label": "Microsoft Foundry Blog",
        "kind": "Announcements",
        "url": "https://devblogs.microsoft.com/foundry/",
        "feed_url": "https://devblogs.microsoft.com/foundry/feed/",
        "authority": "Launch context",
    },
    {
        "id": "agent-framework",
        "label": "Microsoft Agent Framework",
        "kind": "SDK releases",
        "url": "https://github.com/microsoft/agent-framework/releases",
        "feed_url": "https://github.com/microsoft/agent-framework/releases.atom",
        "authority": "Canonical SDK releases",
    },
    {
        "id": "foundry-local",
        "label": "Foundry Local",
        "kind": "Runtime releases",
        "url": "https://github.com/microsoft/Foundry-Local/releases",
        "feed_url": "https://github.com/microsoft/Foundry-Local/releases.atom",
        "authority": "Canonical runtime releases",
    },
]

SOURCE_BY_ID = {source["id"]: source for source in SOURCE_DEFINITIONS}
MATURITY_TAGS = ("Retirement", "Deprecated", "GA", "Preview")
TRACKING_PARAMS = {"cid", "ocid", "source", "WT.mc_id"}


class LearnMetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.metadata: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "meta":
            return
        values = {key.lower(): value for key, value in attrs if value is not None}
        name = values.get("name") or values.get("property")
        content = values.get("content")
        if name and content:
            self.metadata[name.lower()] = content


def build_session() -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
    )
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def strip_html(text: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(without_tags)).strip()


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key not in TRACKING_PARAMS and not key.lower().startswith("utm_")
        ]
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def make_item_id(source: str, url: str, title: str, date: str) -> str:
    value = f"{source}|{canonicalize_url(url)}|{title.strip().lower()}|{date}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def trimmed_excerpt(body: str, limit: int = 320) -> str:
    clean = strip_html(body)
    if len(clean) <= limit:
        return clean
    shortened = clean[: limit + 1].rsplit(" ", 1)[0].rstrip(" ,.;:")
    return f"{shortened}..."


def summarize(session: requests.Session, title: str, body: str, source: str) -> str:
    fallback = trimmed_excerpt(body) or title
    if not GH_TOKEN:
        return fallback
    try:
        response = session.post(
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
                            "Summarize Microsoft Foundry product updates for developers in one or "
                            "two sentences, at most 70 words. State exactly what changed, include "
                            "availability such as GA or preview only when explicit, and explain why "
                            "it matters. Do not speculate, use hype, or add emojis."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Source: {source}\nTitle: {title}\n\n"
                            f"Content:\n{strip_html(body)[:5000]}"
                        ),
                    },
                ],
                "temperature": 0.2,
                "max_tokens": 160,
            },
            timeout=60,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"].strip()
        return content or fallback
    except (
        requests.RequestException,
        KeyError,
        IndexError,
        TypeError,
        ValueError,
    ) as exc:
        print(f"  warning: summary generation failed ({exc}); using source excerpt", file=sys.stderr)
        return fallback


def guess_tags(title: str, source: str, body: str = "", raw_tags: list[str] | None = None) -> list[str]:
    text = " ".join([title, body, *(raw_tags or [])]).lower()
    maturity_text = " ".join([title, *(raw_tags or [])]).lower()
    tags: list[str] = []

    if any(term in maturity_text for term in ("retire", "deprecated", "deprecation", "end of support")):
        tags.append("Retirement")
    if "generally available" in maturity_text or re.search(r"\bga\b", maturity_text):
        tags.append("GA")
    if any(term in maturity_text for term in ("preview", "early access")):
        tags.append("Preview")

    if source == "agent-framework":
        tags.append("SDK")
    if source == "foundry-local":
        tags.append("Local")

    category_rules = [
        ("Models", ("model", "gpt", "claude", "phi", "llama", "grok", "inference", "fine-tun")),
        ("Agents", ("agent", "routine", "memory", "autopilot")),
        ("Tools", ("toolbox", "mcp", "tool search", "work iq", "fabric iq", "browser automation")),
        ("Knowledge", ("foundry iq", "retrieval", "search", "knowledge", "rag")),
        ("Evaluation", ("eval", "optimizer", "red team", "quality", "rubric")),
        ("Observability", ("observab", "trace", "monitor", "telemetry", "roi")),
        ("Security", ("security", "guardrail", "purview", "content safety", "pii", "entra", "rbac")),
        ("Voice", ("voice", "speech", "audio")),
        ("Local", ("foundry local", "on-device", "edge", "sovereign", "air-gap")),
        ("SDK", ("sdk", "python-", "dotnet-", "java", "typescript", "azure-ai-projects")),
    ]
    for tag, terms in category_rules:
        if any(term in text for term in terms):
            tags.append(tag)

    if not tags:
        tags.append("Platform")

    return list(dict.fromkeys(tags))[:4]


def parse_entry_date(entry: feedparser.FeedParserDict) -> datetime:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return datetime.now(timezone.utc)
    return datetime(*parsed[:6], tzinfo=timezone.utc)


def is_relevant_azure_update(
    title: str,
    description: str,
    categories: list[str],
) -> bool:
    normalized_categories = {category.lower() for category in categories}
    if "microsoft foundry" in normalized_categories or "azure openai service" in normalized_categories:
        return True
    title_text = title.lower()
    return any(
        term in title_text
        for term in (
            "microsoft foundry",
            "azure ai foundry",
            "foundry agent",
            "foundry local",
            "foundry model",
            "azure openai",
        )
    )


def fetch_feed_items(
    session: requests.Session,
    source: dict[str, str],
    cutoff: datetime,
    existing_ids: set[str],
    existing_urls: set[str],
) -> tuple[list[dict[str, object]], int]:
    response = session.get(source["feed_url"], timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    parsed = feedparser.parse(response.content)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"invalid feed: {parsed.bozo_exception}")
    if not parsed.entries:
        raise ValueError("feed returned no entries")

    additions: list[dict[str, object]] = []
    considered = 0
    for entry in parsed.entries[:200]:
        title = strip_html(entry.get("title", "Untitled"))
        body = entry.get("summary", "") or (
            entry.get("content", [{}])[0].get("value", "") if entry.get("content") else ""
        )
        raw_tags = [
            strip_html(tag.get("term", ""))
            for tag in entry.get("tags", [])
            if tag.get("term")
        ]
        if source["id"] == "azure-updates" and not is_relevant_azure_update(
            title,
            strip_html(body),
            raw_tags,
        ):
            continue

        published = parse_entry_date(entry)
        if published < cutoff:
            continue
        considered += 1
        url = canonicalize_url(entry.get("link", ""))
        if not url:
            continue
        date = published.strftime("%Y-%m-%d")
        item_id = make_item_id(source["id"], url, title, date)
        if item_id in existing_ids or url in existing_urls:
            continue
        additions.append(
            {
                "id": item_id,
                "date": date,
                "title": title,
                "summary": summarize(session, title, body, source["label"]),
                "url": url,
                "source": source["id"],
                "source_label": source["label"],
                "tags": guess_tags(title, source["id"], strip_html(body), raw_tags),
            }
        )
        existing_ids.add(item_id)
        existing_urls.add(url)
        print(f"  new: {title}")
    return additions, considered


def fetch_learn_item(
    session: requests.Session,
    source: dict[str, str],
    cutoff: datetime,
    existing_ids: set[str],
) -> tuple[list[dict[str, object]], int]:
    response = session.get(source["url"], timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    parser = LearnMetadataParser()
    parser.feed(response.text)
    metadata = parser.metadata

    title = metadata.get("og:title") or metadata.get("title")
    description = metadata.get("description") or metadata.get("og:description")
    date_value = metadata.get("ms.date") or metadata.get("updated_at")
    if not title or not description or not date_value:
        raise ValueError("Microsoft Learn page is missing title, description, or update date metadata")

    updated = datetime.fromisoformat(date_value.replace("Z", "+00:00"))
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    date = updated.astimezone(timezone.utc).strftime("%Y-%m-%d")
    url = canonicalize_url(source["url"])
    item_id = make_item_id(source["id"], url, title, date)
    if updated < cutoff or item_id in existing_ids:
        return [], 1

    existing_ids.add(item_id)
    return [
        {
            "id": item_id,
            "date": date,
            "title": title.replace(" - Microsoft Foundry | Microsoft Learn", ""),
            "summary": description,
            "url": url,
            "source": source["id"],
            "source_label": source["label"],
            "tags": ["Platform", "Documentation"],
        }
    ], 1


def infer_source(item: dict[str, object]) -> str:
    source = str(item.get("source", ""))
    url = str(item.get("url", "")).lower()
    if source in SOURCE_BY_ID:
        return source
    if "agent-framework" in url:
        return "agent-framework"
    if "foundry-local" in url:
        return "foundry-local"
    if "azure.microsoft.com" in url and "/updates" in url:
        return "azure-updates"
    if "learn.microsoft.com" in url:
        return "foundry-docs"
    return "foundry-blog"


def normalize_existing_item(item: dict[str, object]) -> dict[str, object]:
    normalized = dict(item)
    source = infer_source(normalized)
    date = str(normalized.get("date", ""))
    title = str(normalized.get("title", "Untitled"))
    url = canonicalize_url(str(normalized.get("url", "")))
    normalized["source"] = source
    normalized["source_label"] = SOURCE_BY_ID[source]["label"]
    normalized["url"] = url
    normalized["id"] = normalized.get("id") or make_item_id(source, url, title, date)
    tags = list(dict.fromkeys(normalized.get("tags") or ["Platform"]))
    inferred_tags = guess_tags(title, source)
    explicit_maturity = [tag for tag in MATURITY_TAGS if tag in inferred_tags]
    if source == "azure-updates":
        tags = [tag for tag in tags if tag not in MATURITY_TAGS]
    elif source in {"agent-framework", "foundry-local"}:
        tags = [
            tag
            for tag in tags
            if tag not in {"Preview", "Retirement", "Deprecated"} or tag in explicit_maturity
        ]
    required_tags = []
    if source == "agent-framework":
        required_tags.append("SDK")
    if source == "foundry-local":
        required_tags.append("Local")
    normalized["tags"] = list(
        dict.fromkeys([*explicit_maturity, *required_tags, *tags])
    )[:4]
    return normalized


def source_status(
    source: dict[str, str],
    checked_at: str,
    status: str,
    items_seen: int,
    error: str | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "id": source["id"],
        "label": source["label"],
        "kind": source["kind"],
        "url": source["url"],
        "authority": source["authority"],
        "status": status,
        "checked_at": checked_at,
        "items_seen": items_seen,
    }
    if error:
        result["error"] = error[:240]
    return result


def write_step_summary(
    statuses: list[dict[str, object]],
    added: int,
    total: int,
    failures: list[str],
) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    lines = [
        "## Foundry update refresh",
        "",
        f"- Added **{added}** new item(s)",
        f"- Feed now contains **{total}** item(s)",
        "",
        "| Source | Status | Items seen |",
        "| --- | --- | ---: |",
    ]
    lines.extend(
        f"| {status['label']} | {status['status']} | {status['items_seen']} |"
        for status in statuses
    )
    if failures:
        lines.extend(["", f"Failures: {', '.join(failures)}"])
    try:
        with open(summary_path, "a", encoding="utf-8") as summary_file:
            summary_file.write("\n".join(lines) + "\n")
    except OSError as exc:
        print(f"warning: could not write workflow summary: {exc}", file=sys.stderr)


def main() -> None:
    news = json.loads(NEWS_FILE.read_text(encoding="utf-8"))
    items = [normalize_existing_item(item) for item in news.get("items", [])]
    existing_ids = {str(item["id"]) for item in items}
    existing_urls = {str(item["url"]) for item in items if item.get("url")}
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    checked_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    session = build_session()

    additions: list[dict[str, object]] = []
    statuses: list[dict[str, object]] = []
    failures: list[str] = []

    for source in SOURCE_DEFINITIONS:
        print(f"Checking {source['label']}: {source['url']}")
        try:
            if source["id"] == "foundry-docs":
                new_items, items_seen = fetch_learn_item(
                    session,
                    source,
                    cutoff,
                    existing_ids,
                )
            else:
                new_items, items_seen = fetch_feed_items(
                    session,
                    source,
                    cutoff,
                    existing_ids,
                    existing_urls,
                )
            additions.extend(new_items)
            statuses.append(source_status(source, checked_at, "ok", items_seen))
        except (requests.RequestException, ValueError) as exc:
            message = f"{source['label']}: {exc}"
            failures.append(message)
            statuses.append(source_status(source, checked_at, "error", 0, str(exc)))
            print(f"  error: {message}", file=sys.stderr)

    items.extend(additions)
    items.sort(key=lambda item: (str(item.get("date", "")), str(item.get("title", ""))), reverse=True)
    news.update(
        {
            "schema_version": 2,
            "last_checked": checked_at,
            "last_updated": (
                datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if additions
                else news.get("last_updated", "")
            ),
            "item_count": min(len(items), MAX_ITEMS),
            "sources": statuses,
            "items": items[:MAX_ITEMS],
        }
    )
    NEWS_FILE.write_text(
        json.dumps(news, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    write_step_summary(statuses, len(additions), len(news["items"]), failures)

    if failures:
        raise SystemExit(f"{len(failures)} source check(s) failed; see errors above")
    print(f"Refresh complete: added {len(additions)} item(s); {len(news['items'])} total.")


if __name__ == "__main__":
    main()
