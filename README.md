# EzyTrail Codex Plugins

Company plugin marketplace for [Codex App](https://openai.com/codex). Ships the **ezyhub** plugin, which connects your Codex App to the EzyTrail company platform:

- **Company model gateway** — enroll once, get a personal gateway key issued and managed by the company control plane
- **Role-based skills** — company skills for your role, synced automatically and kept up to date
- **Approved MCP tools** — company knowledge base search, pre-configured

## Quick Start

The easiest way to install is to paste this to your Codex agent:

> Install the EzyHub company plugin. Follow the "Instructions for AI agents" section of https://github.com/X-InsightAI/codex-plugins exactly.

The agent installs the plugin and runs enrollment. Your only manual step is signing in with your company Google account when the browser opens, then opening a new Codex thread at the end.

### Manual install

```bash
codex plugin marketplace add https://github.com/X-InsightAI/codex-plugins
codex plugin add ezyhub@ezytrail
```

Then open a new Codex thread and run `/enroll`.

## Instructions for AI agents

You are installing a company plugin on an employee machine. Follow these steps exactly. Do not improvise alternative install paths.

**Rules**

- Never print, log, or paste the gateway key anywhere (chat, URLs, files). The enroll helper handles all key material itself.
- Do not install `uv`, create a virtualenv, or install any Python packages — every helper script is Python stdlib-only.
- Use `python3` on macOS/Linux and `python` on Windows.
- Do not harvest keys from any existing Codex/CLIProxyAPI config. Keys are issued only by the enrollment backend.
- Do not modify the user's `auth.json` or sign them out of ChatGPT. Enrollment coexists with a ChatGPT login.

**Step 1 — Prerequisites**

Check that the `codex` CLI is available (`codex --version`) and that `git` and Python 3.11+ are on PATH. If `codex` is missing, stop and tell the user to install Codex App first.

**Step 2 — Install the plugin**

```bash
codex plugin marketplace add https://github.com/X-InsightAI/codex-plugins
codex plugin add ezyhub@ezytrail
```

**Step 3 — Enroll**

Clone this repository into a temporary directory and run the one-shot enroll helper from the clone (the helper configures `CODEX_HOME`, so where it runs from does not matter):

```bash
git clone --depth 1 https://github.com/X-InsightAI/codex-plugins <tmpdir>/ezytrail-plugins
cd <tmpdir>/ezytrail-plugins
python3 plugins/ezyhub/scripts/ezyhub_backend.py enroll-backend
```

A browser window opens. Tell the user to sign in with their **company Google account** and click **"Authorize Codex"**. The script waits for them and then finishes on its own: it configures the Codex provider and key, syncs role skills and the company MCP server, and installs a background auto-sync job.

If enrollment fails partway after the key is configured, the helper prints the exact resume command (`sync-skills` or `install-auto-sync`). Run that printed command — do not invent a different recovery.

**Step 4 — Verify**

```bash
python3 plugins/ezyhub/scripts/ezyhub_backend.py doctor
```

All employee-facing checks should pass (admin-only checks report "skipped" — that is normal).

**Step 5 — Hand back to the user**

Tell the user: **open a new Codex thread** (or quit and reopen Codex App). The new provider, skills, and MCP tools are only picked up by new threads. Enrollment is complete.

## Skills

Once installed, these skills are available in Codex App:

| Skill | What it does |
| --- | --- |
| `/enroll` | Enroll this machine with the company gateway (browser sign-in, key issue, skill + MCP sync, auto-sync install) |
| `/key-status` | Show current enrollment status and key metadata |
| `/key-rotate` | Rotate the gateway key and reconfigure Codex |
| `/sync-skills` | Sync role-based company skills into the local Codex skills directory |
| `/kb` | Query the company knowledge base through the approved MCP server |

## Updating

```bash
codex plugin add ezyhub@ezytrail
```

Then open a new Codex thread. Role skills also refresh automatically in the background (default: every 4 hours).

## How it works

- Your gateway key is issued by the company control plane during `/enroll` and stored only on your device (in `CODEX_HOME/.env`). This repository contains no secrets.
- Company skills are synced under the `ezyhub-` prefix and never touch your personal skills.
- Role-based skill content is served per-role by the company backend — it is not stored in this repository.
- Managed by the EzyTrail platform team. The source of truth for skills and backend services lives in a private repository.
