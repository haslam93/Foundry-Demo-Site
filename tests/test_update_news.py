import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from xml.etree import ElementTree as ET

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import update_news as news


def response(data=None, text="", url="https://learn.microsoft.com/azure/foundry/overview"):
    return SimpleNamespace(json=lambda: data, text=text, content=text.encode(), url=url,
                           raise_for_status=lambda: None)


def github_release(tag, date):
    return {"tag_name": tag, "name": tag, "prerelease": False, "draft": False,
            "published_at": date, "body": "Upstream release note.",
            "html_url": f"https://github.com/microsoft/agent-framework/releases/tag/{tag}"}


class UrlAndDateTests(unittest.TestCase):
    def test_canonical_urls_drop_tracking_not_meaningful_queries(self):
        self.assertEqual(news.canonicalize_url("https://learn.microsoft.com/path/?view=foundry&UTM_Source=x&WT.mc_id=y#part"),
                         "https://learn.microsoft.com/path?view=foundry")

    def test_empty_relative_and_unsafe_urls_are_rejected(self):
        for value in ("", "/relative", "javascript:alert(1)", "http://example.com", "https://user:pass@example.com"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                news.canonicalize_url(value)

    def test_missing_feed_date_is_never_today(self):
        with self.assertRaises(ValueError):
            news.parse_entry_date({})

    def test_dates_use_utc(self):
        self.assertEqual(news.iso(news.parse_date("2026-09-07T23:00:00-04:00")), "2026-09-08T03:00:00Z")

    def test_maturity_not_inferred_from_release_body(self):
        tags = news.guess_tags("python-1.17.0", "agent-framework", "preview API fixes now generally available")
        self.assertNotIn("GA", tags)
        self.assertNotIn("Preview", tags)
        self.assertIn("SDK", tags)

    def test_gateway_azure_announcements_are_relevant(self):
        self.assertTrue(news.is_relevant_azure_update("API Management AI gateway", "MCP support", []))
        self.assertFalse(news.is_relevant_azure_update("Azure SQL backups", "routine maintenance", ["SQL"]))

    def test_excerpt_strips_markup(self):
        text = news.trimmed_excerpt("## Added\n[Tracing](https://example.com) <b>support</b>")
        self.assertEqual(text, "Added Tracing support")


class AdapterTests(unittest.TestCase):
    def test_extension_tags_are_not_core_framework_versions(self):
        source = news.SOURCE_BY_ID["agent-framework"]
        self.assertIsNone(news.github_component(source, "python-hosting-a2a-1.0.0a260723"))
        self.assertEqual(news.github_component(source, "python-1.17.0"), "Agent Framework / Python")
        self.assertEqual(news.github_component(news.SOURCE_BY_ID["foundry-local"], "cli-preview-0.10.3"),
                         "Foundry Local / CLI")

    def test_channel_version_does_not_regress_on_a_maintenance_release(self):
        source = news.SOURCE_BY_ID["agent-framework"]
        rows = [news.release_snapshot(source, "Python", version, "stable", news.parse_date(date),
                                       f"https://github.com/microsoft/agent-framework/releases/tag/{version}")
                for version, date in (("python-1.9.8", "2026-09-07"), ("python-2.0.0", "2026-09-01"))]
        self.assertEqual(news.latest_channels(rows)[0]["version"], "python-2.0.0")

    def test_github_token_only_sent_to_github_api(self):
        session = Mock()
        session.get.return_value = response({})
        with patch.dict(os.environ, {"GITHUB_TOKEN": "test-only-not-a-credential"}):
            news.get_json(session, "https://pypi.org/pypi/test/json")
            self.assertNotIn("Authorization", session.get.call_args.kwargs["headers"])
            news.get_json(session, "https://api.github.com/repos/microsoft/agent-framework/releases")
            self.assertIn("Authorization", session.get.call_args.kwargs["headers"])

    def test_retries_are_get_only(self):
        session = news.build_session()
        self.assertEqual(session.adapters["https://"].max_retries.allowed_methods, frozenset({"GET"}))
        session.close()

    def test_both_languages_and_channels_survive_github_releases(self):
        source = news.SOURCE_BY_ID["agent-framework"]
        rows = [
            {"tag_name": tag, "name": tag, "prerelease": pre, "draft": False,
             "published_at": date, "html_url": f"https://github.com/microsoft/agent-framework/releases/tag/{tag}",
             "body": "Small upstream release note."}
            for tag, pre, date in (
                ("python-2.0.0rc1", True, "2026-09-06T00:00:00Z"),
                ("python-1.17.0", False, "2026-09-03T00:00:00Z"),
                ("dotnet-1.20.0", False, "2026-08-31T00:00:00Z"))
        ]
        with patch.object(news, "get_json", return_value=rows):
            items, releases, warnings = news.fetch_github(Mock(), source, news.parse_date("2026-08-01"))
        self.assertEqual(len(items), 3)
        self.assertEqual(len(releases), 3)
        self.assertEqual({row["channel"] for row in releases}, {"stable", "prerelease"})
        self.assertEqual(warnings, [])

    def test_github_channels_are_discovered_beyond_the_news_window_and_first_three_pages(self):
        pages = [[github_release("python-1.9.8", "2026-07-01T00:00:00Z")] * 100] * 3
        pages.append([github_release("python-2.0.0", "2026-03-01T00:00:00Z"),
                      github_release("dotnet-1.20.0", "2026-02-01T00:00:00Z")])
        with patch.object(news, "get_json", side_effect=pages) as fetch:
            items, releases, warnings = news.fetch_github(
                Mock(), news.SOURCE_BY_ID["agent-framework"], news.parse_date("2026-08-01"))
        self.assertEqual(fetch.call_count, 4)
        self.assertEqual(items, [])
        self.assertEqual({row["version"] for row in releases}, {"python-2.0.0", "dotnet-1.20.0"})
        self.assertEqual(warnings, [])

    def test_incomplete_github_pagination_is_bounded_and_reported(self):
        rows = [github_release("python-1.9.8", "2026-09-01T00:00:00Z")] * 100
        with patch.object(news, "MAX_GITHUB_RELEASE_PAGES", 2), \
                patch.object(news, "get_json", return_value=rows) as fetch:
            items, releases, warnings = news.fetch_github(
                Mock(), news.SOURCE_BY_ID["agent-framework"], news.parse_date("2026-08-01"))
        self.assertEqual(fetch.call_count, 2)
        self.assertTrue(items)
        self.assertEqual(releases[0]["version"], "python-1.9.8")
        self.assertIn("partial", warnings[0])

    def test_pypi_stable_version_not_latest_uploaded_maintenance_version(self):
        data = {"info": {"version": "2.0.0"}, "releases": {
            "1.9.5": [{"upload_time_iso_8601": "2026-09-06T00:00:00Z", "yanked": False}],
            "2.0.0": [{"upload_time_iso_8601": "2026-09-01T00:00:00Z", "yanked": False}],
            "3.0.0b1": [{"upload_time_iso_8601": "2026-09-05T00:00:00Z", "yanked": False}],
            "9.0.0": [{"upload_time_iso_8601": "2026-09-07T00:00:00Z", "yanked": True}],
        }}
        with patch.object(news, "get_json", return_value=data):
            items, releases, _ = news.fetch_pypi(Mock(), news.SOURCE_BY_ID["projects-sdk"], news.parse_date("2026-08-01"))
        self.assertEqual(next(row["version"] for row in releases if row["channel"] == "stable"), "2.0.0")
        self.assertNotIn("9.0.0", [row["version"] for row in releases])
        self.assertEqual(len(items), 3)

    def test_learn_prefers_actual_update_timestamp(self):
        session = Mock()
        session.get.return_value = response(text=(
            '<meta name="title" content="Foundry docs"><meta name="description" content="Current guidance">'
            '<meta name="ms.date" content="2026-06-01"><meta name="updated_at" content="2026-09-07T05:00:00Z">'
            '<meta name="git_commit_id" content="abcdef">'))
        metadata = news.fetch_learn_metadata(session, "https://learn.microsoft.com/azure/foundry/overview")
        self.assertEqual(metadata["updated_at"], "2026-09-07T05:00:00Z")
        self.assertEqual(metadata["revision"], "abcdef")

    def test_empty_feed_fails_loudly(self):
        session = Mock()
        session.get.return_value = response(text="<html>error page</html>")
        with self.assertRaises(ValueError):
            news.fetch_feed(session, news.SOURCE_BY_ID["foundry-blog"], news.parse_date("2026-01-01"))

    def test_undated_feed_item_is_reported_and_not_published(self):
        session = Mock()
        session.get.return_value = response(text="<rss/>")
        entry = {"title": "Foundry news", "link": "https://devblogs.microsoft.com/foundry/test", "summary": "Test."}
        with patch.object(news.feedparser, "parse", return_value=SimpleNamespace(entries=[entry])):
            items, _, warnings = news.fetch_feed(session, news.SOURCE_BY_ID["foundry-blog"], news.parse_date("2026-01-01"))
        self.assertEqual(items, [])
        self.assertTrue(warnings)


class PersistenceTests(unittest.TestCase):
    def make_item(self, title, day, url="https://learn.microsoft.com/azure/foundry/whats-new-foundry"):
        return news.item(news.SOURCE_BY_ID["foundry-docs"], title, url, news.parse_date(day), "Summary")

    def test_rolling_learn_url_replaces_old_snapshot(self):
        old, current = self.make_item("July", "2026-07-01"), self.make_item("August", "2026-08-01")
        self.assertEqual(news.merge_items([old], [current]), [current])

    def test_merge_is_idempotent(self):
        row = self.make_item("Current", "2026-09-01")
        self.assertEqual(news.merge_items([row], [row, row]), [row])

    def test_failed_fetch_keeps_old_items(self):
        row = self.make_item("Known release", "2026-09-01")
        self.assertEqual(news.merge_items([row], []), [row])

    def test_rss_escapes_markup_and_preserves_publication_date(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "feed.xml"
            row = self.make_item("A < B & C", "2026-09-01")
            news.write_rss([row], "2026-09-07T00:00:00Z", path)
            root = ET.parse(path)
            self.assertEqual(root.findtext("./channel/item/title"), "A < B & C")
            self.assertIn("01 Sep 2026", root.findtext("./channel/item/pubDate"))

    def test_resource_failure_preserves_last_good_metadata(self):
        resource = {"id": "guide", "url": "https://learn.microsoft.com/azure/foundry/overview",
                    "reviewed_at": "2026-09-07"}
        previous = {"resources": [{"id": "guide", "last_success_at": "2026-09-06T00:00:00Z", "revision": "old"}]}
        with patch.object(news, "check_resource", side_effect=ValueError("Missing metadata")):
            health, changes = news.refresh_resources(Mock(), {"resources": [resource]}, previous, "2026-09-07T00:00:00Z")
        self.assertEqual(health["resources"][0]["revision"], "old")
        self.assertEqual(health["resources"][0]["last_success_at"], "2026-09-06T00:00:00Z")
        self.assertEqual(health["resources"][0]["status"], "error")
        self.assertEqual(changes, [])

    def test_resource_change_requires_editorial_review(self):
        resource = {"id": "guide", "title": "Guide", "url": "https://learn.microsoft.com/azure/foundry/overview",
                    "topics": ["Hosted agents"], "reviewed_at": "2026-09-07"}
        metadata = {"title": "Guide", "description": "Description", "revision": "old",
                    "updated_at": "2026-09-06T00:00:00Z", "canonical_url": resource["url"]}
        with patch.object(news, "check_resource", return_value=metadata):
            baseline, changes = news.refresh_resources(Mock(), {"resources": [resource]}, {}, "2026-09-07T00:00:00Z")
        self.assertEqual(changes, [])
        metadata["revision"] = "new"
        with patch.object(news, "check_resource", return_value=metadata):
            updated, changes = news.refresh_resources(Mock(), {"resources": [resource]}, baseline, "2026-09-08T00:00:00Z")
        self.assertTrue(updated["resources"][0]["review_needed"])
        self.assertEqual(changes[0]["date_kind"], "updated")
        with patch.object(news, "check_resource", return_value=metadata):
            unchanged, changes = news.refresh_resources(Mock(), {"resources": [resource]}, updated, "2026-09-09T00:00:00Z")
        self.assertTrue(unchanged["resources"][0]["review_needed"])
        self.assertEqual(changes, [])
        resource["reviewed_at"] = "2026-09-09"
        with patch.object(news, "check_resource", return_value=metadata):
            reviewed, _ = news.refresh_resources(Mock(), {"resources": [resource]}, unchanged, "2026-09-09T00:00:00Z")
        self.assertFalse(reviewed["resources"][0]["review_needed"])

    def run_partial_refresh(self, old, github):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "news.json").write_text(json.dumps(old), encoding="utf-8")
            (root / "catalog.json").write_text('{"resources":[]}', encoding="utf-8")
            empty = ([], [], [])
            with (
                patch.object(news, "ROOT", root),
                patch.object(news, "NEWS_FILE", root / "news.json"),
                patch.object(news, "CATALOG_FILE", root / "catalog.json"),
                patch.object(news, "HEALTH_FILE", root / "resource-health.json"),
                patch.object(news, "refresh_resources", return_value=({"schema_version": 1, "resources": []}, [])),
                patch.object(news, "fetch_feed", return_value=empty),
                patch.object(news, "fetch_learn", return_value=empty),
                patch.object(news, "fetch_pypi", return_value=empty),
                patch.object(news, "fetch_github", github),
                patch.object(news.sys, "stdout", io.StringIO()),
                patch.object(news.sys, "stderr", io.StringIO()),
                patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": ""}),
                self.assertRaises(SystemExit),
            ):
                news.main()
            result = json.loads((root / "news.json").read_text(encoding="utf-8"))
            self.assertTrue((root / "resource-health.json").exists())
            self.assertTrue((root / "feed.xml").exists())
            return result

    def test_partial_refresh_writes_failure_status_before_exiting_and_keeps_release(self):
        row = self.make_item("Known documentation", "2026-09-01")
        snapshot = news.release_snapshot(news.SOURCE_BY_ID["agent-framework"], "Agent Framework / Python",
                                         "python-1.17.0", "stable", news.parse_date("2026-09-03"),
                                         "https://github.com/microsoft/agent-framework/releases/tag/python-1.17.0")
        old = {"items": [row], "releases": [snapshot], "sources": [
            {"id": "agent-framework", "last_success_at": "2026-09-06T00:00:00Z"}]}
        result = self.run_partial_refresh(old, Mock(side_effect=news.requests.Timeout()))
        failed = next(source for source in result["sources"] if source["id"] == "agent-framework")
        self.assertEqual(failed["status"], "error")
        self.assertEqual(failed["last_success_at"], "2026-09-06T00:00:00Z")
        self.assertEqual(result["releases"], [snapshot])
        self.assertEqual(result["items"][0]["id"], row["id"])

    def test_incomplete_release_refresh_preserves_quiet_channels_and_the_highest_known_version(self):
        source = news.SOURCE_BY_ID["agent-framework"]
        python = news.release_snapshot(source, "Agent Framework / Python", "python-2.0.0", "stable",
                                       news.parse_date("2026-03-01"), f"{source['url']}/tag/python-2.0.0")
        dotnet = news.release_snapshot(source, "Agent Framework / .NET", "dotnet-1.20.0", "stable",
                                       news.parse_date("2026-04-01"), f"{source['url']}/tag/dotnet-1.20.0")
        for version in ("python-1.9.8", "python-2.1.0"):
            with self.subTest(version=version):
                candidate = news.release_snapshot(
                    source, "Agent Framework / Python", version, "stable",
                    news.parse_date("2026-09-07"), f"{source['url']}/tag/{version}")
                signal = news.item(source, version, candidate["url"], news.parse_date("2026-09-07"), "Release note.")
                old = {"items": [], "releases": [python, dotnet], "sources": [
                    {"id": source["id"], "last_success_at": "2026-09-06T00:00:00Z"}]}
                adapter = Mock(side_effect=lambda session, current, cutoff:
                               ([signal], [candidate], ["Release discovery is partial."])
                               if current["id"] == source["id"] else ([], [], []))
                result = self.run_partial_refresh(old, adapter)
                status = next(row for row in result["sources"] if row["id"] == source["id"])
                self.assertEqual(status["status"], "warning")
                self.assertEqual(status["last_success_at"], "2026-09-06T00:00:00Z")
                expected = python if version == "python-1.9.8" else candidate
                self.assertCountEqual(result["releases"], [expected, dotnet])
                self.assertEqual(result["items"][0]["id"], signal["id"])


if __name__ == "__main__":
    unittest.main()
