# Maintaining the Foundry field guide

## Editorial contract

`content/catalog.json` is the small, curated knowledge layer. A resource includes a stable `id`, official HTTPS URL, original summary, practical outcome, type/topic tags, and `reviewed_at` date. Playbook steps and lab scenarios reference those IDs rather than duplicating URLs.

Prefer Microsoft Learn for the current implementation contract, the exact model's documentation for model-specific restrictions, official release communications for launch status, and repository releases for package changes. A repository sample or a page without a preview banner is **not evidence of feature GA**.

Keep model limits, package versions, CLI requirements, and region lists out of undated static claims. Link the current reference and use generated release snapshots. When illustrating architecture, say which components are actually deployed.

## Adding a source or resource

1. Open the official source and confirm its current title, scope, and implementation requirements.
2. Add a resource to `content/catalog.json` with an original summary and the specific outcome it supports. Use topic names already present so filters remain useful.
3. For an official GitHub repository, add `repo`. For a particular sample directory, also add `repo_path`; the updater checks the path exists and watches commits affecting it.
4. Reference the resource from a relevant playbook or scenario where it adds practical value. Do not grow an undifferentiated link dump.
5. Run the source refresh, offline contracts, and relevant browser coverage before publishing.

The refresher's official-organization allowlist is deliberate. Extend it only for a verified official publisher. Tokens are attached only to GitHub API requests, never to arbitrary linked URLs.

## Freshness and provenance

| Field | Meaning |
| --- | --- |
| News `date` / `published_at` | Upstream publication, modification, or observed date, identified by `date_kind` |
| News `first_seen_at` | When this guide first added that signal; used for local read tracking |
| `last_checked` / resource `checked_at` | When the updater attempted the source check |
| `last_success_at` | Most recent successful source check, retained across failures |
| Resource `source_updated_at` | Upstream Learn modification time or watched repository commit time, when available |
| Resource `reviewed_at` | When the curated explanation was reviewed, not when a request returned HTTP 200 |
| `review_needed` | The watched source differs from the baseline for the last recorded editorial review |

Learn's `updated_at` is preferred over `ms.date`, which can represent a different editorial date. Missing publication dates are not replaced with today's date. A rolling what's-new URL has one current snapshot; old titles are not retained behind a URL that now describes something else.

For revision-backed sources, change detection fingerprints the source revision, modification time, and resolved URL, not transient HTML banners. Non-versioned pages are checked for reachability and are not advertised as fully monitored semantic changes.

After a source changes, review the affected resource and playbooks, update the explanation if necessary, and advance `reviewed_at`. On the next refresh the watcher records the new review baseline. Do not advance review dates just to hide warnings.

## Release channels

GitHub release snapshots use the explicit `prerelease` flag and separate Agent Framework's `python-` and `dotnet-` tags. They are not inferred from a changelog mentioning a preview tool. The updater paginates releases instead of assuming the most recent mixed-language release represents both SDKs.

Channel discovery scans beyond the news lookback so a quiet SDK or older higher
major version cannot disappear behind recent maintenance releases. Pagination is
bounded at 20 pages per repository. If that bound is reached before the end, the
source is visibly partial, previous channels are retained, and only higher
observed versions replace them; the last fully successful check is not advanced.
Inspect the repository and adjust the bound when necessary rather than treating
an incomplete inventory as current.

PyPI's current `info.version` identifies the current stable package, rather than treating a freshly uploaded maintenance build as the latest major line. Yanked distributions are excluded. Individual evaluators and APIs can remain preview inside stable packages.

The daily RSS feed contains short excerpts and links, not mirrored articles or full source documents.

## Failure handling and recovery

An upstream timeout, non-success HTTP response, missing expected metadata, malformed payload, or invalid date is visible. Per-source failure does not delete that source's previous feed items or release snapshot. Per-resource failure retains its last-known successful metadata.

Healthy source results and failure status are written before the updater exits nonzero. The refresh workflow validates them before committing. The Pages workflow listens to refresh completion even on failure so the public site can show the problem. A bot-maintained issue links the failing run; successful refresh/publication closes it.

If the updater fails before it can write status, the previous snapshot remains, and the browser's 48-hour stale threshold eventually makes the missed refresh visible. GitHub workflow failure notifications and the issue cover the immediate failure.

For recovery: inspect the failing source, update only the affected adapter/URL, add an offline regression case, run the refresh, and confirm publication. Do not make network restrictions permissive or paste private credentials into the repository just to get a green badge.

## Delivery boundary

`scripts/build_site.py` packages an explicit public file allowlist. Do not replace it with uploading the repository root: deployment tooling or local environment files must never become public Pages assets.

The `workflow_run` build checks out `main`, not upstream artifacts or untrusted workflow references. Azure deployment is a separate, manual, environment-bound workflow. Ordinary site updates and daily source refreshes must never trigger model inference or Azure provisioning.
