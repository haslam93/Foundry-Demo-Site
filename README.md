# Hammad's Foundry Updates Portal

A living, single-page guide to **Microsoft Foundry** and the **Microsoft Agent Framework** — models, agents, hosted agents, Foundry IQ, and Day 2 operations (evaluations, red teaming, observability, governance) — with a live demo wired to a real Foundry project in Sweden Central.

**Live site:** https://haslam93.github.io/Foundry-Demo-Site/

## What's inside

- `index.html` — the entire site: platform guide, Anthropic Claude section, Agent Framework deep dive, Day 2 ops, and an interactive console that talks to real Foundry agents via the Responses API.
- `news.json` — the "What's New" feed rendered at the top of the site.
- `scripts/update_news.py` — pulls the [Foundry dev blog](https://devblogs.microsoft.com/foundry/) RSS and [microsoft/agent-framework](https://github.com/microsoft/agent-framework) releases, summarizes new items with GitHub Models, and merges them into `news.json`.
- `.github/workflows/weekly-foundry-updates.yml` — runs the updater **every Monday** (and on demand via *Run workflow*), committing changes so GitHub Pages redeploys automatically.

## Live demo section

The "Live from Sweden Central" section calls a real Foundry project (`foundry-showcase` on `swedenfoundry93`) directly from the browser. To use it, paste a short-lived Entra token obtained with:

```bash
az account get-access-token --scope https://ai.azure.com/.default --query accessToken -o tsv
```

Tokens expire in about an hour and never leave the page. Only identities with access to the Foundry project can invoke the agents.

## Updating manually

Run the updater locally:

```bash
pip install requests feedparser
python scripts/update_news.py
```

Without a `GITHUB_TOKEN`, summaries fall back to article excerpts.
