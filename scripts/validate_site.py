"""Offline contracts for the static site and its generated source snapshots."""

import json
from datetime import timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

from update_news import ROOT, SOURCE_BY_ID, canonicalize_url, now_utc, parse_date


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def unique_ids(rows, label):
    ids = [row["id"] for row in rows]
    require(all(isinstance(value, str) and value for value in ids), f"{label}: empty ID")
    require(len(ids) == len(set(ids)), f"{label}: duplicate IDs")
    return set(ids)


def date_check(value, label):
    require(parse_date(value) <= now_utc() + timedelta(days=1), f"{label}: future date")


class SiteParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.links = []
        self.assets = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(values["id"])
        if tag == "a" and values.get("href"):
            self.links.append(values["href"])
        if tag == "script" and values.get("src"):
            self.assets.append(values["src"])
        if tag == "link" and values.get("rel") == "stylesheet":
            self.assets.append(values["href"])


def validate(root=ROOT):
    news = load(root / "news.json")
    catalog = load(root / "content" / "catalog.json")
    health = load(root / "resource-health.json")
    demo = load(root / "demo" / "config.json")
    require(news["schema_version"] == 3, "Unsupported news schema")
    require(catalog["schema_version"] == health["schema_version"] == demo["schema_version"] == 1,
            "Unsupported catalog, health or demo schema")
    date_check(news["last_checked"], "news.last_checked")
    require(news["items"], "An empty feed must not replace the last good feed")
    require(news["item_count"] == len(news["items"]), "Incorrect item count")
    unique_ids(news["items"], "news")
    require(unique_ids(news["sources"], "sources") == set(SOURCE_BY_ID), "Missing source adapter/status")
    require(news["stale_after_hours"] >= news["refresh_interval_hours"] > 0, "Invalid refresh policy")
    dates = [row["date"] for row in news["items"]]
    require(dates == sorted(dates, reverse=True), "News must be newest first")
    for row in news["items"]:
        require(row["source"] in SOURCE_BY_ID, "Unknown news source")
        require(row["title"] and row["summary"], "Empty news title or summary")
        canonicalize_url(row["url"])
        date_check(row["date"], row["id"])
        require(row["date_kind"] in {"published", "updated", "observed"}, "Unknown date provenance")
        require(isinstance(row["tags"], list) and row["tags"], "News tags must be a nonempty list")
    for row in news["sources"]:
        require(row["status"] in {"ok", "warning", "error"}, "Unknown source status")
        date_check(row["checked_at"], row["id"])
        if row.get("last_success_at"):
            date_check(row["last_success_at"], row["id"])
        if row["status"] == "error":
            require(row.get("error"), "Failed source must say why")
    release_keys = []
    for row in news["releases"]:
        require(row["source"] in SOURCE_BY_ID, "Unknown release source")
        require(row["channel"] in {"stable", "prerelease"}, "Unknown release channel")
        canonicalize_url(row["url"])
        date_check(row["published_at"], row["component"])
        release_keys.append((row["component"], row["channel"]))
    require(len(release_keys) == len(set(release_keys)), "Duplicate release channel")
    resource_ids = unique_ids(catalog["resources"], "resources")
    for row in catalog["resources"]:
        require(all(row.get(key) for key in ("title", "summary", "outcome", "topics", "type", "reviewed_at")),
                f"Incomplete resource: {row['id']}")
        canonicalize_url(row["url"])
        date_check(row["reviewed_at"], row["id"])
    require(unique_ids(health["resources"], "resource health") == resource_ids, "Resource health coverage mismatch")
    for row in health["resources"]:
        require(row["status"] in {"ok", "error"}, "Unknown resource status")
        date_check(row["checked_at"], row["id"])
        if row["status"] == "error":
            require(row.get("error"), "Failed resource must say why")
    unique_ids(catalog["playbooks"], "playbooks")
    scenario_ids = unique_ids(catalog["scenarios"], "scenarios")
    for book in catalog["playbooks"]:
        require(book["steps"] and book["pitfalls"], f"Incomplete playbook: {book['id']}")
        require(book["scenario_id"] in scenario_ids, f"Missing playbook scenario: {book['id']}")
        for step in book["steps"]:
            require(step["resource_ids"] and set(step["resource_ids"]) <= resource_ids,
                    f"Missing step resource in {book['id']}")
    for scenario in catalog["scenarios"]:
        require(scenario["prompt"] and scenario["expected"], f"Incomplete scenario: {scenario['id']}")
        require(set(scenario["resource_ids"]) <= resource_ids, f"Missing scenario resource: {scenario['id']}")
    canonicalize_url(demo["project_endpoint"])
    require(demo["project_endpoint"].endswith("/api/projects/" + demo["project_name"]),
            "Demo endpoint does not match the configured project")
    require(demo["authentication_scope"] == "https://ai.azure.com/.default", "Unexpected demo auth scope")
    parser = SiteParser()
    parser.feed((root / "index.html").read_text(encoding="utf-8"))
    require(len(parser.ids) == len(set(parser.ids)), "Duplicate HTML IDs")
    for link in parser.links:
        if link.startswith("#"):
            require(unquote(link[1:]) in parser.ids, f"Broken section link: {link}")
        elif not urlsplit(link).scheme:
            require((root / unquote(urlsplit(link).path)).is_file(), f"Missing local link: {link}")
        else:
            canonicalize_url(link)
    for asset in parser.assets:
        require(not urlsplit(asset).scheme, "Scripts and styles must be self-hosted")
        require((root / asset).is_file(), f"Missing site asset: {asset}")
    rss = ET.parse(root / "feed.xml")
    require(rss.getroot().tag == "rss" and rss.findall("./channel/item"), "Invalid or empty RSS feed")
    print(f"Site contracts passed: {len(news['items'])} signals, {len(resource_ids)} resources, "
          f"{len(catalog['playbooks'])} playbooks, {len(catalog['scenarios'])} demo scenarios.")


if __name__ == "__main__":
    validate()
