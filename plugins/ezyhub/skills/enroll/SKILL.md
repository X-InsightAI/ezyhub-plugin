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

`enroll-backend` is one command that does the whole enrollment chain: it creates the enroll session, opens the browser, waits for the employee to complete Google sign-in and click "Authorize Codex" in EzyHub, then configures Codex, syncs role skills and the KB MCP server, and installs a background auto-sync job — all in one run.

```bash
python3 plugins/ezyhub/scripts/ezyhub_backend.py enroll-backend
```

On Windows, use `python` instead of `python3` if that is the available launcher.

What happens after the browser step completes:

1. Codex provider/key are configured (see "Provider migration" below).
2. Role skills and the EzyHub KB MCP server config are synced (same as `/sync-skills`; see `plugins/ezyhub/skills/sync-skills/SKILL.md` for collision rules). Skip with `--skip-sync-skills`.
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

## Provider migration

Enrollment must always configure the provider as `ezyhub`, not `company` or
`ezyapis`. The public gateway base URL remains `https://api.ezyapis.com/v1`.

Required local Codex shape:

```toml
model_provider = "ezyhub"

[model_providers.ezyhub]
name = "EzyHub"
base_url = "https://api.ezyapis.com/v1"
wire_api = "responses"
env_key = "EZYHUB_CODEX_KEY"
```

Store the employee CLIProxyAPI key in `CODEX_HOME/.env` as `EZYHUB_CODEX_KEY`.
Do not store the EzyHub key in `auth.json`, and do not remove the user's
OpenAI/ChatGPT login tokens from `auth.json`. It is valid for Codex App to stay
logged in to OpenAI while the active model provider is `ezyhub`.

When configuring Codex, migrate old managed runtime state:

- remove or replace `[model_providers.company]`
- remove or replace `[model_providers.ezyapis]`
- remove old inline `experimental_bearer_token` values under those managed
  provider sections
- leave unrelated providers, MCP servers, and MCP `bearer_token_env_var`
  settings alone

If an EzyHub-owned local skill or script still reads `[model_providers.ezyapis]`
or uses the provider name `ezyapis`, update it to `ezyhub`. Keep the domain
`api.ezyapis.com` unchanged; that is the public gateway host, not the provider
identity.

## Production path

Google identity verification is owned by the EzyHub app, not by key-backend directly. Under the hood, `enroll-backend` (see "One-shot enroll" above) does:

1. `POST /enroll/sessions` on key-backend
2. open a browser to the returned `browser_url`, which points at the EzyHub app (`{EZYHUB_APP_BASE_URL}/codex/enroll?session_id=...`)
3. the employee signs in with their company Google account inside EzyHub and clicks "Authorize Codex"; EzyHub verifies the identity and calls key-backend's `POST /admin/enroll-sessions/{session_id}/approve` to issue the key
4. poll `/enroll/sessions/{session_id}/result`
5. configure Codex using the returned one-time key result, then sync skills/MCP and install auto-sync (steps 2-3 under "One-shot enroll")

The long-lived CLIProxyAPI key must never appear in browser URLs or logs.
Open a new Codex App thread after enrollment. If the new thread still sees stale
skills, tools, or provider config, quit and reopen Codex App.
