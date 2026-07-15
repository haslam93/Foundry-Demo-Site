# Hammad's Microsoft Foundry Field Guide

A living, single-page guide to **Microsoft Foundry**: official updates, source health, platform capabilities, Agent Framework releases, decision guidance, and a live demo wired to a real Foundry project in Sweden Central.

**Live site:** https://haslam93.github.io/Foundry-Demo-Site/

## What's inside

- `index.html` — the site, including a searchable update timeline, official source registry, platform guide, Agent Framework deep dive, Day 2 operations, capability cheat sheet, and Responses API console.
- `news.json` — versioned feed data plus source-check status and refresh timestamps.
- `scripts/update_news.py` — validates and merges updates from:
  - [Azure Updates](https://azure.microsoft.com/updates/?products=ai-foundry) for release status.
  - [Microsoft Learn](https://learn.microsoft.com/azure/foundry/whats-new-foundry) for current product guidance.
  - [Microsoft Foundry Blog](https://devblogs.microsoft.com/foundry/) for announcements and monthly roundups.
  - [Microsoft Agent Framework releases](https://github.com/microsoft/agent-framework/releases) for SDK changes.
  - [Foundry Local releases](https://github.com/microsoft/Foundry-Local/releases) for local runtime changes.
- `.github/workflows/weekly-foundry-updates.yml` — runs every Monday and on demand. It caches pinned dependencies, retries source requests, validates generated JSON, and exposes source health in the workflow summary. If one source fails, healthy-source updates and the failure status are still published, then the job fails visibly for follow-up.

## Live demo section

The "Live from Sweden Central" section calls a real Foundry project (`foundry-showcase` on `swedenfoundry93`) directly from the browser. To use it, paste a short-lived Entra token obtained with:

```bash
az account get-access-token --scope https://ai.azure.com/.default --query accessToken -o tsv
```

Tokens expire in about an hour and never leave the page. Only identities with access to the Foundry project can invoke the agents.

## Updating manually

Run the updater locally:

```bash
python -m pip install -r requirements.txt
python scripts/update_news.py
```

Without a `GITHUB_TOKEN`, summaries use official source excerpts. In GitHub Actions, the repository token can use GitHub Models to create concise developer-focused summaries.
