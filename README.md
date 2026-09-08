# Hammad's Microsoft Foundry Field Guide

**[Open the field guide](https://haslam93.github.io/Foundry-Demo-Site/)** · [Daily refresh](https://github.com/haslam93/Foundry-Demo-Site/actions/workflows/weekly-foundry-updates.yml) · [Site deployment](https://github.com/haslam93/Foundry-Demo-Site/actions/workflows/site.yml) · [RSS](https://haslam93.github.io/Foundry-Demo-Site/feed.xml)

A practical, independent home for **Microsoft Foundry and Microsoft Agent Framework**: current release signals, curated official sources, deployment runbooks, runnable sample collections, and an authenticated lab connected to the existing Azure project.

## Use it

- **Daily brief:** separate Python/.NET, Foundry Local, Projects SDK, and Evaluation SDK release channels, with actual publication dates and visible source health.
- **Resource library:** 46 curated guides, references, and sample collections covering hosted agents, Agent Framework, deployment, API Management AI gateway, MCP/A2A, Foundry IQ, model choice, evaluation, tracing, safety, networking, and local inference.
- **Six outcome-based playbooks:** first agent, hosted deployment, Agent Framework orchestration, AI gateway, evaluation/observability, and knowledge grounding. Check off steps, download a runbook, or load its scenario into the lab.
- **Personal navigation:** Ctrl+K / Cmd+K search, topic/type filters, shareable filtered URLs, local reading lists, read tracking, and light/dark themes. No analytics, advertising, or account requirement.
- **Azure lab:** list the actual deployed agent versions, choose a guided scenario, attach a dated source snapshot, inspect a token-free API request, and see completion/latency/token usage. Conversation state is isolated by agent version and cleared on reconnect.

This is a curated resource, **not a complete product changelog or an official Microsoft publication**. Open each source for its current availability and support constraints.

## How it stays current

`Daily Foundry intelligence` runs every day at **06:23 UTC** and on demand. Its filename remains `weekly-foundry-updates.yml` to preserve existing workflow links.

| Input | What is tracked |
| --- | --- |
| Azure Updates | Foundry, Azure OpenAI, Agent Framework, and relevant AI gateway announcements |
| Microsoft Learn | The rolling what's-new document and the curated guide/reference pages |
| Foundry Blog | Official announcements and launch context |
| Agent Framework GitHub releases | Separate Python/.NET stable and prerelease snapshots |
| Foundry Local GitHub releases | Separate runtime and CLI release channels |
| PyPI | Published `azure-ai-projects` and `azure-ai-evaluation` package versions |
| Official sample repositories | Commits affecting the watched repository or sample directory |

The updater uses bounded GET retries, strict HTTPS URLs, real upstream dates, deduplication, a 120-day fetch window, and a maximum of 240 retained signals. Release snapshots are separate from the bounded news archive. Summaries are short source excerpts or deterministic metadata, **not generated availability claims**. No GitHub Models token or paid inference is needed for updates.

Checks and publications are deliberately different. A quiet source is not fabricated into a new announcement. A failed source retains its last-known content; other healthy sources can still publish. Failures appear in the site, Actions summary, and a bot-maintained issue that closes on recovery. After 48 hours without a successful check, the site shows stale status.

An open, visible tab reloads the published release/health snapshots approximately every 15 minutes; **Refresh data** does the same immediately without calling Azure. Dependabot proposes weekly updates for workflow actions, Python dependencies, and development-only browser tooling; those changes remain subject to review and CI.

Curated prose is not silently rewritten when a source changes. The watcher flags it for editorial review and publishes a source-change signal. This prevents a fresh timestamp from falsely claiming an old explanation was reviewed. See [source maintenance](docs/source-maintenance.md).

### Pages delivery

The site uses **GitHub Actions Pages deployment**, not implicit branch/Jekyll publishing. `site.yml` runs for main-branch changes, pull requests, manual dispatch, and completion of the refresh workflow. The explicit `workflow_run` trigger matters: commits made with `GITHUB_TOKEN` do not trigger normal push workflows.

The pipeline runs offline Python/JavaScript contracts and headless browser scenarios, then packages only public runtime files into `_site`. Deployment manifests, tests, source tooling, `.azure`, and credentials are not uploaded. A partially failed source refresh can publish visible failure status; invalid site data cannot be deployed.

GitHub schedules are best-effort and can be delayed or disabled by repository settings/inactivity. The on-page timestamps and **Run workflow** action make this observable and recoverable.

## Azure lab and deployment

The configured project is `foundry-showcase` on `swedenfoundry93` in Sweden Central. Public configuration lives in `demo/config.json`; no access token is part of that file.

The lab retains the direct, caller-authenticated connection to Foundry. To connect, sign in with Azure CLI and obtain a short-lived token:

```powershell
az account get-access-token --scope https://ai.azure.com/.default --query accessToken -o tsv
```

Paste it only into the lab's password field. The token is cleared from the field on connect, held in module memory, and sent over HTTPS **to the configured Foundry endpoint**. It is not stored in localStorage or sent to GitHub. Your Azure RBAC still applies. Prompts/responses are sent to Azure and may be retained by the service; use non-sensitive demo data.

Opening the page, choosing a scenario, and listing agents do not initiate model inference. **Send to Azure** is explicitly billable, output-bounded, and never retried automatically. Stopping the browser request does not guarantee server-side cancellation. A completed smoke response is not a quality evaluation.

The separate, manual **Azure demo deployment** workflow manages the new, deployed `field-guide-coach` prompt agent using an existing model. It does not provision a hosted runtime, models, or an APIM instance, and it must not overwrite the original concierge, research, or triage agents. GitHub OIDC replaces long-lived client secrets. The main-only `azure-demo` environment and tenant/subscription variables are prepared; its dedicated federated identity and `AZURE_CLIENT_ID` are the remaining setup. See [Azure setup, deployment, costs, and rollback](docs/azure-demo.md) for the exact instructions and workflow behavior.

The gateway runbook explains how to add APIM; the site's architecture illustration does **not** claim a gateway has already been deployed.

## Develop locally

The public site is vanilla HTML/CSS/JavaScript with no client-side framework or runtime CDN. Python refreshes/validates source data; Playwright is development-only.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
npm ci
npx playwright install chromium
python -m unittest discover -s tests -p "test_*.py"
npm test
python scripts\build_site.py
npm run test:browser
python -m http.server 8080 --directory _site --bind 127.0.0.1
```

Open `http://127.0.0.1:8080`. Serve over HTTP rather than opening `index.html` as a file: the site uses modules and relative JSON requests. Browser coverage also exercises the `/Foundry-Demo-Site/` Pages subpath.

To refresh public sources manually:

```powershell
python scripts\update_news.py
python scripts\validate_site.py
```

`GITHUB_TOKEN` is optional locally and is sent only to `api.github.com`. An authenticated personal token may need organization SSO approval; anonymous reads are supported for these public repositories and have lower rate limits. The scheduled workflow uses its repository-provided token.

## Repository map

| Path | Purpose |
| --- | --- |
| `index.html`, `assets/` | Accessible resource hub, local preferences, and authenticated lab |
| `content/catalog.json` | Curated resources, playbooks, and guided demo scenarios |
| `news.json`, `resource-health.json`, `feed.xml` | Generated signals, release snapshots, source health, and RSS |
| `scripts/update_news.py` | Official-source adapters and change detection |
| `scripts/validate_site.py`, `scripts/build_site.py` | Offline data/link contracts and allowlisted Pages artifact |
| `demo/`, `scripts/deploy_demo.py` | Public project configuration and separately managed demo-agent delivery |
| `tests/` | Offline adapter/frontend/deployment checks and mocked browser flows |
| `.github/workflows/` | Daily refresh, tested Pages deployment, and opt-in Azure delivery |
