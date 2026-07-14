<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="plugins/ezyhub/assets/logo-dark.png">
    <img src="plugins/ezyhub/assets/logo.png" alt="EzyTrail" width="96">
  </picture>
</p>

# EzyHub Codex Plugin

Company plugin marketplace for [Codex App](https://openai.com/codex). Ships the **ezyhub** plugin, which connects your Codex App to the EzyHub company platform:

- **Company model gateway** — enroll once, get a personal gateway key issued and managed by the company control plane
- **Role-based skills** — company skills for your role, synced automatically and kept up to date

## Quick Start

The easiest way to install is to paste this to your Codex agent:

> Install the EzyHub company plugin. Using shell commands only (no web search): `git clone --depth 1 https://github.com/X-InsightAI/ezyhub-plugin` into `~/.ezyhub/ezyhub-plugin` (Windows: `%USERPROFILE%\.ezyhub\ezyhub-plugin`), then read `README.md` in the clone and follow its "Instructions for AI agents" exactly.

The agent installs the plugin and runs enrollment. Your only manual step is signing in with your company Google account when the browser opens, then opening a new Codex thread at the end.

### Manual install

```bash
codex plugin marketplace add https://github.com/X-InsightAI/ezyhub-plugin
codex plugin add ezyhub@ezytrail
```

Then open a new Codex thread and run `/enroll`.

## Instructions for AI agents

You are installing a company plugin on an employee machine. Follow these steps exactly. Do not improvise alternative install paths.

**Rules**

- Work with shell commands and local files only. Do not use web search or browsing tools at any point — everything you need is in this repository.
- Installation needs the network: `git clone`, `codex plugin ...`, and the enroll helper all make network requests. If your shell policy blocks one of these commands, ask the user to approve it or to run that one command themselves — do not hunt for offline copies or alternative sources.
- Never print, log, or paste the gateway key anywhere (chat, URLs, files). The enroll helper handles all key material itself.
- Do not install anything to run the helper scripts — they have zero dependencies. Run them as-is with the runtime you already have.
- Do not harvest keys from any existing Codex/CLIProxyAPI config. Keys are issued only by the enrollment backend.
- Do not modify the user's `auth.json` or sign them out of ChatGPT. Enrollment coexists with a ChatGPT login.

**Step 1 — Prerequisites**

Check that the `codex` CLI is available (`codex --version`) and that `git` is on PATH. On Windows the CLI ships inside Codex App and is usually **not** on PATH — use the newest version directory under `%LOCALAPPDATA%\OpenAI\Codex\bin\<version>\codex.exe` and call it by full path. Only if Codex App itself is not installed, stop and tell the user to install it first.

**Step 2 — Install the plugin**

```bash
codex plugin marketplace add https://github.com/X-InsightAI/ezyhub-plugin
codex plugin add ezyhub@ezytrail
```

**Step 3 — Enroll**

Run the one-shot enroll helper from the clone of this repository (clone it now if you haven't already). The clone must be at a persistent location such as `~/.ezyhub/ezyhub-plugin`, not a temporary directory: the auto-sync job installed at the end keeps running the helper from this path.

```bash
git clone --depth 1 https://github.com/X-InsightAI/ezyhub-plugin ~/.ezyhub/ezyhub-plugin
cd ~/.ezyhub/ezyhub-plugin
python3 plugins/ezyhub/scripts/ezyhub_backend.py enroll-backend
```

A browser window opens. Tell the user to sign in with their **company Google account** and click **"Authorize Codex"**. The script waits for them and then finishes on its own: it configures the Codex provider and key, syncs role skills, and installs a background auto-sync job.

If enrollment fails partway after the key is configured, the helper prints the exact resume command (`sync-skills` or `install-auto-sync`). Run that printed command — do not invent a different recovery.

**Step 4 — Verify**

```bash
python3 plugins/ezyhub/scripts/ezyhub_backend.py doctor
```

This live-tests the enrolled key against the company model gateway (the `gateway` check) and confirms the key backend is reachable and auto-sync is installed. All employee-facing checks should pass; admin-only checks report "skipped" — that is normal.

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

## Updating

```bash
codex plugin add ezyhub@ezytrail
```

Then open a new Codex thread. Role skills also refresh automatically in the background (default: every 4 hours).

## How it works

- Your gateway key is issued by the company control plane during `/enroll` and stored only on your device (in `CODEX_HOME/.env`). This repository contains no secrets.
- Company skills are synced under the `ezyhub-` prefix and never touch your personal skills.
- Role-based skill content is served per-role by the company backend — it is not stored in this repository.
- Managed by the EzyHub platform team. The source of truth for skills and backend services lives in a private repository.
