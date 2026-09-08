"""Refresh official release signals and check the field guide's source material."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from xml.etree import ElementTree as ET

import feedparser
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parent.parent
NEWS_FILE = ROOT / "news.json"
CATALOG_FILE = ROOT / "content" / "catalog.json"
HEALTH_FILE = ROOT / "resource-health.json"
LOOKBACK_DAYS = 120
MAX_ITEMS = 240
MAX_GITHUB_RELEASE_PAGES = 20
REQUEST_TIMEOUT = (8, 30)
SITE_URL = "https://haslam93.github.io/Foundry-Demo-Site/"
USER_AGENT = f"Foundry-Field-Guide/3.0 (+{SITE_URL})"
TRACKING_PARAMS = {"cid", "ocid", "source", "wt.mc_id"}

SOURCE_DEFINITIONS = [
    {
        "id": "azure-updates", "label": "Azure Updates", "kind": "Release status",
        "url": "https://azure.microsoft.com/updates/?products=ai-foundry",
        "feed_url": "https://www.microsoft.com/releasecommunications/api/v2/Azure/rss",
        "adapter": "rss", "authority": "Availability announcements",
    },
    {
        "id": "foundry-docs", "label": "Microsoft Learn", "kind": "What's new",
        "url": "https://learn.microsoft.com/azure/foundry/whats-new-foundry",
        "adapter": "learn", "authority": "Current product guidance, not a release feed",
    },
    {
        "id": "foundry-blog", "label": "Microsoft Foundry Blog", "kind": "Announcements",
        "url": "https://devblogs.microsoft.com/foundry/",
        "feed_url": "https://devblogs.microsoft.com/foundry/feed/",
        "adapter": "rss", "authority": "Launch context",
    },
    {
        "id": "agent-framework", "label": "Microsoft Agent Framework", "kind": "SDK releases",
        "url": "https://github.com/microsoft/agent-framework/releases",
        "repo": "microsoft/agent-framework", "adapter": "github",
        "authority": "Repository release channel; not feature GA status",
    },
    {
        "id": "foundry-local", "label": "Foundry Local", "kind": "Runtime releases",
        "url": "https://github.com/microsoft/Foundry-Local/releases",
        "repo": "microsoft/Foundry-Local", "adapter": "github",
        "authority": "Repository release channel",
    },
    {
        "id": "projects-sdk", "label": "Azure AI Projects SDK", "kind": "Python package",
        "url": "https://pypi.org/project/azure-ai-projects/",
        "package": "azure-ai-projects", "adapter": "pypi",
        "authority": "Published package versions; individual APIs may be preview",
    },
    {
        "id": "evaluation-sdk", "label": "Azure AI Evaluation SDK", "kind": "Python package",
        "url": "https://pypi.org/project/azure-ai-evaluation/",
        "package": "azure-ai-evaluation", "adapter": "pypi",
        "authority": "Published package versions; individual evaluators may be preview",
    },
    {
        "id": "resource-watch", "label": "Documentation & sample watch", "kind": "Source changes",
        "url": "https://learn.microsoft.com/azure/foundry/",
        "adapter": "resources", "authority": "Documentation edits and commits, not product launches",
    },
]
SOURCE_BY_ID = {source["id"]: source for source in SOURCE_DEFINITIONS}


class LearnMetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.metadata: dict[str, str] = {}

    def handle_starttag(self, tag, attrs) -> None:
        if tag != "meta":
            return
        values = {key.lower(): value for key, value in attrs if value is not None}
        name = values.get("name") or values.get("property")
        if name and values.get("content"):
            self.metadata[name.lower()] = values["content"]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_date(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def build_session() -> requests.Session:
    retry = Retry(total=2, connect=2, read=2, backoff_factor=1,
                  status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=frozenset({"GET"}), respect_retry_after_header=False)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def get_json(session, url, params=None):
    headers = {"Accept": "application/json"}
    # Never forward the repository token to feeds, PyPI, Learn, or linked URLs.
    if urlsplit(url).hostname == "api.github.com":
        headers["X-GitHub-Api-Version"] = "2022-11-28"
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    response = session.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme.lower() != "https" or not parts.hostname or parts.username or parts.password:
        raise ValueError("Source URLs must be absolute HTTPS URLs without credentials")
    query = urlencode([(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
                       if key.lower() not in TRACKING_PARAMS and not key.lower().startswith("utm_")])
    return urlunsplit(("https", parts.netloc.lower(), parts.path.rstrip("/") or "/", query, ""))


def make_item_id(source: str, url: str, title: str, date: str) -> str:
    return hashlib.sha256(f"{source}|{canonicalize_url(url)}|{title.lower()}|{date}".encode()).hexdigest()[:20]


def trimmed_excerpt(body: str, limit: int = 300) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", body or "")
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = strip_html(re.sub(r"[`#*]", "", text))
    text = re.sub(r"\s+The post .+ appeared first on .+$", "", text)
    return text if len(text) <= limit else text[:limit + 1].rsplit(" ", 1)[0].rstrip(" ,.;:") + "..."


def guess_tags(title: str, source: str, body: str = "", raw_tags=None) -> list[str]:
    text = " ".join([title, body, *(raw_tags or [])]).lower()
    maturity = " ".join([title, *(raw_tags or [])]).lower()
    tags = []
    if any(word in maturity for word in ("retire", "deprecat", "end of support")):
        tags.append("Retirement")
    if "generally available" in maturity or re.search(r"\bga\b", maturity):
        tags.append("GA")
    if any(word in maturity for word in ("preview", "early access")):
        tags.append("Preview")
    if source in {"agent-framework", "projects-sdk", "evaluation-sdk"}:
        tags.append("SDK")
    if source == "foundry-local":
        tags.append("Local")
    rules = [
        ("Hosted agents", ("hosted agent", "hosting", "container", "code deployment")),
        ("API gateway", ("api management", "gateway", "apim")),
        ("Deployment", ("deploy", "azd", "ci/cd")),
        ("Evaluation", ("eval", "optimizer", "red team", "rubric")),
        ("Observability", ("observab", "trac", "monitor", "telemetry")),
        ("Agent Framework", ("agent framework", "python-", "dotnet-")),
        ("Agents", ("agent", "routine", "memory")),
        ("Tools & MCP", ("toolbox", "mcp", "a2a", "tool search", "skills")),
        ("Knowledge", ("foundry iq", "retrieval", "search", "knowledge", "rag")),
        ("Security", ("secur", "guardrail", "content safety", "entra", "rbac", "private")),
        ("Models", ("model", "gpt", "claude", "phi-", "inference", "fine-tun")),
        ("Local", ("foundry local", "on-device")),
    ]
    for tag, terms in rules:
        if any(term in text for term in terms):
            tags.append(tag)
    return list(dict.fromkeys(tags or ["Platform"]))[:6]


def item(source, title, url, published, summary, tags=None, date_kind="published", **extra):
    date = published.strftime("%Y-%m-%d")
    url = canonicalize_url(url)
    return {
        "id": make_item_id(source["id"], url, title, date),
        "date": date, "published_at": iso(published), "date_kind": date_kind,
        "title": title, "summary": summary or title, "url": url,
        "source": source["id"], "source_label": source["label"],
        "tags": tags or guess_tags(title, source["id"], summary), **extra,
    }


def is_relevant_azure_update(title, description, categories) -> bool:
    text = " ".join([title, *categories]).lower()
    return any(term in text for term in
               ("foundry", "azure openai", "agent framework", "ai gateway")) or (
        "api management" in text and any(term in (title + " " + description).lower()
                                         for term in ("ai", "agent", "mcp", "token", "llm")))


def parse_entry_date(entry) -> datetime:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        raise ValueError("Missing publication/update date; not substituting today's date")
    return datetime(*parsed[:6], tzinfo=timezone.utc)


def fetch_feed(session, source, cutoff):
    response = session.get(source["feed_url"], timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    parsed = feedparser.parse(response.content)
    if not parsed.entries:
        raise ValueError("Feed returned no parseable entries")
    items, warnings = [], []
    for entry in parsed.entries[:300]:
        title = strip_html(entry.get("title", ""))
        body = entry.get("summary", "") or (entry.get("content") or [{}])[0].get("value", "")
        categories = [tag.get("term", "") for tag in entry.get("tags", [])]
        if source["id"] == "azure-updates" and not is_relevant_azure_update(title, body, categories):
            continue
        try:
            published = parse_entry_date(entry)
            url = canonicalize_url(entry.get("link", ""))
            if not title:
                raise ValueError("Missing title")
            if published > now_utc() + timedelta(days=1):
                raise ValueError("Future-dated entry")
        except ValueError as exc:
            warnings.append(f"Skipped entry: {title[:80]} ({exc})")
            continue
        if published >= cutoff:
            items.append(item(source, title, url, published, trimmed_excerpt(body),
                              guess_tags(title, source["id"], strip_html(body), categories),
                              summary_kind="source_excerpt"))
    return items, [], warnings


def fetch_learn_metadata(session, url):
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    parser = LearnMetadataParser()
    parser.feed(response.text)
    metadata = parser.metadata
    title = metadata.get("og:title") or metadata.get("title")
    description = metadata.get("description") or metadata.get("og:description")
    date = metadata.get("updated_at") or metadata.get("ms.date")
    if not title or not description or not date:
        raise ValueError("Learn page lacks its expected title, description or update date")
    return {
        "title": title.replace(" - Microsoft Foundry | Microsoft Learn", ""),
        "description": description, "updated_at": iso(parse_date(date)),
        "revision": metadata.get("git_commit_id", ""),
        "canonical_url": canonicalize_url(response.url),
    }


def fetch_learn(session, source, cutoff):
    metadata = fetch_learn_metadata(session, source["url"])
    published = parse_date(metadata["updated_at"])
    items = [item(source, metadata["title"], source["url"], published,
                  metadata["description"], ["Platform", "Documentation"],
                  date_kind="updated", summary_kind="source_description")] if published >= cutoff else []
    return items, [], []


def github_component(source, tag):
    if source["id"] == "agent-framework":
        for prefix, label in (("python-", "Agent Framework / Python"), ("dotnet-", "Agent Framework / .NET")):
            if re.match(rf"^{prefix}\d", tag.lower()):
                return label
        return None
    return "Foundry Local / CLI" if tag.startswith("cli-") else "Foundry Local"


def release_snapshot(source, component, version, channel, published, url):
    return {"source": source["id"], "component": component, "version": version,
            "channel": channel, "published_at": iso(published), "url": canonicalize_url(url)}


def latest_channels(releases):
    def order(row):
        match = re.search(r"(\d+)\.(\d+)\.(\d+)(.*)$", row["version"])
        if not match:
            return (0, 0, 0, 0, 0, row["published_at"])
        suffix = re.search(r"(dev|a|b|rc|post)(\d+)", match[4], re.I)
        stage = {"dev": 0, "a": 1, "b": 2, "rc": 3, "post": 5}
        return (*map(int, match.group(1, 2, 3)),
                stage[suffix[1].lower()] if suffix else 4,
                int(suffix[2]) if suffix else 0, row["published_at"])
    result = {}
    for release in sorted(releases, key=order, reverse=True):
        result.setdefault((release["component"], release["channel"]), release)
    return list(result.values())


def fetch_github(session, source, cutoff):
    items, releases, warnings = [], [], []
    # Release-channel discovery must outlive the bounded news window.
    for page in range(1, MAX_GITHUB_RELEASE_PAGES + 1):
        rows = get_json(session, f"https://api.github.com/repos/{source['repo']}/releases",
                        {"per_page": 100, "page": page})
        if not isinstance(rows, list):
            raise ValueError("GitHub returned an unexpected releases payload")
        for row in rows:
            if row.get("draft"):
                continue
            published = parse_date(row["published_at"])
            tag = row["tag_name"]
            channel = "prerelease" if row["prerelease"] else "stable"
            component = github_component(source, tag)
            if component:
                releases.append(release_snapshot(source, component, tag, channel, published, row["html_url"]))
            if published >= cutoff:
                tags = guess_tags(tag, source["id"], row.get("body", ""))
                if channel == "prerelease":
                    tags = list(dict.fromkeys(["Prerelease", *tags]))
                items.append(item(source, row.get("name") or tag, row["html_url"], published,
                                  trimmed_excerpt(row.get("body", "")), tags,
                                  summary_kind="release_excerpt", release_channel=channel,
                                  component=component or "Agent Framework integration"))
        if len(rows) < 100:
            break
    else:
        warnings.append(
            f"Release discovery is partial after {MAX_GITHUB_RELEASE_PAGES} pages; "
            "known channel snapshots are retained."
        )
    if not releases and not warnings:
        raise ValueError("No published GitHub releases found")
    return items, latest_channels(releases), warnings


def fetch_pypi(session, source, cutoff):
    data = get_json(session, f"https://pypi.org/pypi/{source['package']}/json")
    stable_version = data["info"]["version"]
    items, releases = [], []
    for version, files in data["releases"].items():
        available = [row for row in files if not row.get("yanked")]
        if not available:
            continue
        published = min(parse_date(row["upload_time_iso_8601"]) for row in available)
        channel = "prerelease" if re.search(r"(a|b|rc|dev)\d", version, re.I) else "stable"
        url = f"https://pypi.org/project/{source['package']}/{version}/"
        # PyPI's info.version identifies the current stable release, even if an old
        # maintenance line was uploaded more recently.
        if channel == "prerelease" or version == stable_version:
            releases.append(release_snapshot(source, source["package"], version, channel, published, url))
        if published >= cutoff:
            tags = ["SDK", "Evaluation" if source["id"] == "evaluation-sdk" else "Agents"]
            if channel == "prerelease":
                tags.append("Prerelease")
            items.append(item(source, f"{source['package']} {version}", url, published,
                              f"Python package {version} was published to PyPI ({channel} channel). "
                              "Review the package's changelog and migration guidance before upgrading; "
                              "package stability does not establish feature availability.",
                              tags, summary_kind="package_metadata", release_channel=channel))
    if not releases:
        raise ValueError("No installable package releases found")
    return items, latest_channels(releases), []


def check_resource(session, resource):
    url = canonicalize_url(resource["url"])
    if resource.get("repo"):
        repo = resource["repo"]
        if not re.fullmatch(r"(?:microsoft|microsoft-foundry|Azure-Samples|azure-ai-foundry|Azure|MicrosoftDocs)/[\w.-]+", repo, re.I):
            raise ValueError("Watched repository must belong to an approved official organization")
        if resource.get("repo_path"):
            get_json(session, f"https://api.github.com/repos/{repo}/contents/{quote(resource['repo_path'], safe='/')}")
        rows = get_json(session, f"https://api.github.com/repos/{repo}/commits",
                        {"per_page": 1, **({"path": resource["repo_path"]} if resource.get("repo_path") else {})})
        if not isinstance(rows, list) or not rows:
            raise ValueError("No commits returned for the watched repository/path")
        row = rows[0]
        return {"canonical_url": url, "revision": row["sha"],
                "updated_at": iso(parse_date(row["commit"]["committer"]["date"])),
                "change_url": canonicalize_url(row["html_url"]),
                "description": resource["summary"], "title": resource["title"]}
    if urlsplit(url).hostname == "learn.microsoft.com":
        return fetch_learn_metadata(session, url)
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return {"canonical_url": canonicalize_url(response.url), "revision": "",
            "updated_at": None, "title": resource["title"], "description": resource["summary"]}


def refresh_resources(session, catalog, previous, checked_at):
    previous_rows = {row["id"]: row for row in previous.get("resources", [])}
    statuses, changes = [], []
    for resource in catalog["resources"]:
        old = previous_rows.get(resource["id"], {})
        status = {
            **old, "id": resource["id"], "url": resource["url"], "checked_at": checked_at,
            "reviewed_at": resource["reviewed_at"],
        }
        try:
            metadata = check_resource(session, resource)
            watched = {key: metadata[key] for key in ("canonical_url", "revision", "updated_at")}
            fingerprint = hashlib.sha256(json.dumps(watched, sort_keys=True).encode()).hexdigest()
            new_review = old.get("reviewed_at") != resource["reviewed_at"]
            baseline = fingerprint if new_review else old.get("reviewed_fingerprint", fingerprint)
            changed = bool(old.get("fingerprint") and old["fingerprint"] != fingerprint)
            status.update({
                "status": "ok", "last_success_at": checked_at,
                "source_updated_at": metadata["updated_at"], "revision": metadata["revision"],
                "canonical_url": metadata["canonical_url"], "fingerprint": fingerprint,
                "reviewed_fingerprint": baseline, "review_needed": fingerprint != baseline,
            })
            status.pop("error", None)
            if changed:
                changed_at = parse_date(metadata["updated_at"] or checked_at)
                status["last_changed_at"] = checked_at
                changes.append(item(
                    SOURCE_BY_ID["resource-watch"],
                    f"{'Sample' if resource.get('repo') else 'Documentation'} changed: {resource['title']}",
                    metadata.get("change_url", resource["url"]), changed_at,
                    "The watched source changed since the previous check. Revisit its instructions "
                    "before using the saved guide. This is a source change, not a product release.",
                    list(dict.fromkeys(["Source change", *resource["topics"]]))[:6],
                    date_kind="updated" if metadata["updated_at"] else "observed",
                    summary_kind="change_detection", resource_id=resource["id"],
                ))
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            status.update({"status": "error", "error": safe_error(exc)})
            print(f"Resource check failed: {resource['id']}: {safe_error(exc)}", file=sys.stderr)
        statuses.append(status)
    return {"schema_version": 1, "checked_at": checked_at, "resources": statuses}, changes


def safe_error(exc) -> str:
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return f"HTTP {exc.response.status_code} from source"
    if isinstance(exc, requests.RequestException):
        return f"Network request failed ({type(exc).__name__})"
    return str(exc).replace("\n", " ")[:200]


def normalize_existing_item(row):
    normalized = dict(row)
    source_id = normalized.get("source", "foundry-blog")
    if source_id not in SOURCE_BY_ID:
        raise ValueError(f"Unknown source in existing feed: {source_id}")
    normalized["url"] = canonicalize_url(normalized["url"])
    parse_date(normalized["date"])
    normalized["id"] = normalized.get("id") or make_item_id(
        source_id, normalized["url"], normalized["title"], normalized["date"])
    normalized["source_label"] = SOURCE_BY_ID[source_id]["label"]
    normalized.setdefault("date_kind", "updated" if source_id == "foundry-docs" else "published")
    normalized.setdefault("summary_kind", "source_excerpt")
    return normalized


def merge_items(existing, incoming):
    # The Learn what's-new page is a rolling URL. Keep only its current snapshot;
    # retaining old titles that lead to new content creates misleading archives.
    keyed = {}
    for row in [*existing, *incoming]:
        key = (row["source"], row["url"])
        if key not in keyed or row["date"] >= keyed[key]["date"]:
            keyed[key] = row
    return sorted(keyed.values(), key=lambda row: (row["date"], row["title"]), reverse=True)[:MAX_ITEMS]


def write_json(path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_rss(items, checked_at, path):
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    for tag, value in (("title", "Hammad's Microsoft Foundry Field Guide"),
                       ("link", SITE_URL),
                       ("description", "Official Foundry releases, SDK versions and source changes."),
                       ("lastBuildDate", format_datetime(parse_date(checked_at), usegmt=True))):
        ET.SubElement(channel, tag).text = value
    for row in items[:60]:
        node = ET.SubElement(channel, "item")
        for tag, value in (("title", row["title"]), ("link", row["url"]),
                           ("description", row["summary"]),
                           ("pubDate", format_datetime(parse_date(row.get("published_at", row["date"])), usegmt=True))):
            ET.SubElement(node, tag).text = value
        ET.SubElement(node, "guid", {"isPermaLink": "false"}).text = row["id"]
        ET.SubElement(node, "category").text = row.get("date_kind", "published")
    ET.indent(rss)
    path.write_bytes(ET.tostring(rss, encoding="utf-8", xml_declaration=True) + b"\n")


def write_step_summary(statuses, added, total, health):
    lines = [
        "## Foundry intelligence refresh", "",
        f"New signals: **{added}**. Retained signals: **{total}**. "
        f"Resources checked: **{len(health['resources'])}**.", "",
        "Publication dates are upstream dates. A successful check does not mean a new release.", "",
        "| Source | Result | Signals in window | Last successful check |",
        "| --- | --- | ---: | --- |",
        *[f"| {row['label']} | {row['status']} | {row['items_seen']} | {row.get('last_success_at', 'Never')} |"
          for row in statuses],
    ]
    problems = [f"- {row['label']}: {row.get('error', '; '.join(row.get('warnings', [])))}"
                for row in statuses if row["status"] != "ok"]
    if problems:
        lines.extend(["", "### Needs attention", *problems])
    summary = "\n".join(lines) + "\n"
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write(summary)


def main():
    news = json.loads(NEWS_FILE.read_text(encoding="utf-8"))
    catalog = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    previous_health = json.loads(HEALTH_FILE.read_text(encoding="utf-8")) if HEALTH_FILE.exists() else {}
    existing = [normalize_existing_item(row) for row in news.get("items", [])]
    old_sources = {row["id"]: row for row in news.get("sources", [])}
    checked_at = iso(now_utc())
    cutoff = now_utc() - timedelta(days=LOOKBACK_DAYS)
    session = build_session()
    health, source_changes = refresh_resources(session, catalog, previous_health, checked_at)
    incoming, statuses = [], []
    releases = news.get("releases", [])
    adapters = {"rss": fetch_feed, "learn": fetch_learn, "github": fetch_github, "pypi": fetch_pypi}
    for source in SOURCE_DEFINITIONS:
        print(f"Checking {source['label']}")
        old = old_sources.get(source["id"], {})
        status = {key: source[key] for key in ("id", "label", "kind", "url", "authority")}
        status.update({"checked_at": checked_at, "last_success_at": old.get("last_success_at"),
                       "items_seen": 0})
        try:
            if source["adapter"] == "resources":
                failed = [row["id"] for row in health["resources"] if row["status"] == "error"]
                rows, snapshots, warnings = source_changes, [], [f"Unreachable resource: {name}" for name in failed]
            else:
                rows, snapshots, warnings = adapters[source["adapter"]](session, source, cutoff)
            incoming.extend(rows)
            if warnings:
                known = [row for row in releases if row["source"] == source["id"]]
                snapshots = latest_channels([*known, *snapshots])
            releases = [row for row in releases if row["source"] != source["id"]] + snapshots
            status.update({"status": "warning" if warnings else "ok", "items_seen": len(rows),
                           "last_success_at": old.get("last_success_at") if warnings else checked_at})
            if warnings:
                status["warnings"] = warnings
            if rows:
                status["latest_published"] = max(row["date"] for row in rows)
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            status.update({"status": "error", "error": safe_error(exc)})
            print(f"Source check failed: {source['label']}: {safe_error(exc)}", file=sys.stderr)
        statuses.append(status)
    items = merge_items(existing, incoming)
    previous_by_url = {(row["source"], row["url"]): row for row in existing}
    for row in items:
        old = previous_by_url.get((row["source"], row["url"]), {})
        row["first_seen_at"] = old.get("first_seen_at", checked_at) if old.get("id") == row["id"] else checked_at
    old_ids = {row["id"] for row in existing}
    added = sum(row["id"] not in old_ids for row in items)
    changed = items != existing
    news.update({
        "schema_version": 3, "last_checked": checked_at,
        "last_updated": max((row["date"] for row in items), default=None),
        "last_content_change_at": checked_at if changed else news.get("last_content_change_at", checked_at),
        "refresh_interval_hours": 24, "stale_after_hours": 48,
        "item_count": len(items), "items": items, "sources": statuses, "releases": releases,
    })
    write_json(HEALTH_FILE, health)
    write_json(NEWS_FILE, news)
    write_rss(items, checked_at, ROOT / "feed.xml")
    write_step_summary(statuses, added, len(items), health)
    if any(row["status"] != "ok" for row in statuses):
        raise SystemExit("Some sources need attention; healthy updates and last-known data were preserved.")


if __name__ == "__main__":
    main()
