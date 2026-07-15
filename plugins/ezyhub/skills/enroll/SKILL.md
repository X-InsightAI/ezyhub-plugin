---
name: enroll
description: Enroll this Codex App with the EzyHub company gateway through the key-backend enroll flow. The backend issues and manages CLIProxyAPI client keys; existing local or WSL client keys are not a key source.
---

# /enroll

Enroll Codex App with EzyHub.

Before changing enrollment behavior or explaining the current design, read
`references/codex-app-history.md`. It is a sanitized record of the user decisions
behind the EzyHub provider, backend key ownership, and Codex App lifecycle.

Do not install `uv` or create a plugin `.venv`. Run the enroll helper with
Codex App's bundled Python when available; otherwise use system `python3` on
macOS/Linux or `python` on Windows.

## One-shot enroll

`enroll-backend` is one command that does the whole enrollment chain: it creates the enroll session, opens the browser, waits for the employee to complete Google sign-in and click "Authorize Codex" in EzyHub, then configures Codex, syncs role skills, and installs a background auto-sync job — all in one run.

```bash
python3 plugins/ezyhub/scripts/ezyhub_backend.py enroll-backend
```

On Windows, use `python` instead of `python3` if that is the available launcher.

## Guiding the employee through the browser step

While `enroll-backend` runs, talk the employee through the browser step in
plain, non-technical language:

1. **Always show the authorization link in the chat, immediately.** The helper
   prints it on a line starting with `AUTHORIZATION LINK`. Paste that URL into
   your reply as a clickable link **every time, even when the browser opened
   fine** — the browser often opens in a profile that is not signed in to
   EzyHub, and the chat link is the only way the employee can reopen it in the
   right profile. The link is not a secret (it carries only a one-time session
   id, never a key).
2. **Browser opens automatically** — tell them a browser window is opening by
   itself. If they are already signed in to EzyHub with their company Google
   account, all they do is click **"Authorize Codex"** on the page.
3. **Wrong profile / page asks them to sign in** — that browser is not signed
   in to EzyHub. Tell them to either sign in there with their **company Google
   account** (`@ezytrail.com.au`), or copy the link from your chat message into
   the browser profile that is already signed in to EzyHub — both work.
4. **No rush** — the helper waits up to 10 minutes for them to finish.

Run the helper so its output streams live (interactive terminal / unbuffered);
if you only see output after the command ends, rerun it interactively — the
link is useless once the wait is over.

What happens after the browser step completes:

1. Codex provider/key are configured (see "Mixed-auth provider config" below).
2. Role skills are synced (same as `/sync-skills`; see `plugins/ezyhub/skills/sync-skills/SKILL.md` for collision rules). Skip with `--skip-sync-skills`.
3. A background job is installed that re-runs the sync automatically, default every 4 hours. Skip with `--skip-auto-sync`, change the interval with `--auto-sync-interval-hours <n>`.

If step 2 or 3 fails after the key is already configured, the helper prints a resume command (`sync-skills` or `install-auto-sync`) rather than leaving the employee stuck mid-flow.

The backend helper defaults to the public control-plane domain `https://codex.ezyapis.com`.
Override with `EZYHUB_CODEX_BACKEND_URL` only when testing another approved domain-backed backend.
Do not use IP addresses in employee/plugin backend URLs.

## Current development path

If the key backend is running in dev mode, use the same one-shot command with `--dev-complete` instead of a real Google login:

```bash
python3 plugins/ezyhub/scripts/ezyhub_backend.py enroll-backend --dev-complete
```

This exercises the same enroll session/result path that production Google OAuth will use, but completes the session with a local dev identity. It still runs the full chain above (config, skill/MCP sync, auto-sync install). The key must be issued by the key backend. If `CLIPROXY_MANAGEMENT_KEY` is configured on key-backend, the backend generates a fresh CLIProxyAPI client key and adds it to CLIProxyAPI through the management API. Do not read an existing client key from WSL or any other local Codex client.

## Mixed-auth provider config

Enrollment is mixed-auth: the employee's OpenAI/ChatGPT login stays, and the
EzyHub key becomes the model bearer as an inline `experimental_bearer_token`
on the provider section. The public gateway base URL remains
`https://api.ezyapis.com/v1`.

The provider id is retained, not migrated: if the active provider is already
EzyHub-managed (`ezyhub`, `company`, or `ezyapis`) or points at the EzyHub
gateway, enrollment keeps that id and rewrites its section clean; only when no
such provider exists does it create `ezyhub`.

Resulting local Codex shape (shown with a retained `ezyapis` id):

```toml
model_provider = "ezyapis"
model = "gpt-5.6-sol"

[features]
image_generation = false

[model_providers.ezyapis]
name = "EzyHub"
base_url = "https://api.ezyapis.com/v1"
wire_api = "responses"
experimental_bearer_token = "<employee key>"
requires_openai_auth = true
```

The employee key is also stored in `CODEX_HOME/.env` as `EZYHUB_CODEX_KEY` for
helper commands; the managed provider section carries the inline token and no
`env_key` line. Do not store the EzyHub key in `auth.json`, and do not remove
the user's OpenAI/ChatGPT login tokens from `auth.json` — `requires_openai_auth
= true` depends on that login staying in place.

When configuring Codex, the helper:

- **first backs up the existing `config.toml`** to
  `config.toml.ezyhub-bak-<UTC-timestamp>` (mode 0600) before any change, and
  writes the new config atomically — the employee's original config is always
  recoverable
- keeps the retained provider id and rewrites its section clean (dropping any
  old `env_key` or stale inline token lines)
- removes the other managed provider sections (`company`, `ezyapis`, `ezyhub`
  — whichever are not the retained id)
- sets `model = "gpt-5.6-sol"` and `image_generation = false` under
  `[features]`
- leaves unrelated providers, MCP servers, and MCP `bearer_token_env_var`
  settings alone

Hard rule for anything touching `config.toml`: only add or replace the
EzyHub-owned pieces listed above. Never delete or rewrite configuration the
employee already has (their providers, MCP servers, feature flags, comments).
Never hand-edit `config.toml` — always go through the helper script, which
enforces the backup and the surgical write.

## Production path

Google identity verification is owned by the EzyHub app, not by key-backend directly. Under the hood, `enroll-backend` (see "One-shot enroll" above) does:

1. `POST /enroll/sessions` on key-backend
2. open a browser to the returned `browser_url`, which points at the EzyHub app (`{EZYHUB_APP_BASE_URL}/codex/enroll?session_id=...`)
3. the employee signs in with their company Google account inside EzyHub and clicks "Authorize Codex"; EzyHub verifies the identity and calls key-backend's `POST /admin/enroll-sessions/{session_id}/approve` to issue the key
4. poll `/enroll/sessions/{session_id}/result`
5. configure Codex using the returned one-time key result, then sync skills/MCP and install auto-sync (steps 2-3 under "One-shot enroll")

The long-lived CLIProxyAPI key must never appear in browser URLs or logs.

After enrollment, tell the employee to **quit Codex App completely and reopen
it** — a full restart is the reliable way to pick up the new provider, key,
and skills. Do not suggest other reload tricks (reload commands, waiting, new
thread only); restart first. No machine reboot is required.
