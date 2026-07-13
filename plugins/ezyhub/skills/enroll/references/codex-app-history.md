# EzyHub Codex App History

This is a sanitized implementation history for the EzyHub Codex App plugin.
It preserves the decisions that matter for future skill behavior. Do not add
real employee keys, OAuth secrets, management keys, refresh tokens, or copied
`auth.json` contents to this file.

## Current target

- The plugin is for Codex App first. Explain enrollment, restart behavior,
  provider selection, and local config in Codex App terms.
- The provider identity is `ezyhub`.
- The model gateway base URL is `https://api.ezyapis.com/v1`.
- Employee/plugin defaults must use domains, not IP addresses.
- The default model used during local validation was `gpt-5.5`.
- The employee key belongs in `CODEX_HOME/.env` as `EZYHUB_CODEX_KEY`.
- `config.toml` should point `[model_providers.ezyhub].env_key` at
  `EZYHUB_CODEX_KEY`.
- `auth.json` is still the OpenAI/ChatGPT login state. Do not delete it, and do
  not remove OpenAI login tokens while configuring the EzyHub provider.

## Provider migration record

The earlier implementation used names such as `company` and `ezyapis` for the
local Codex provider. The chosen provider name is now `ezyhub`. Future enroll and
rotation flows must migrate local runtime state away from old managed provider
sections:

- `[model_providers.company]`
- `[model_providers.ezyapis]`
- inline `experimental_bearer_token` values under those managed sections

Keep `https://api.ezyapis.com/v1` unchanged. That string is the public API host,
not the provider identity. EzyHub-owned skills or scripts that read
`[model_providers.ezyapis]` should be updated to read `[model_providers.ezyhub]`.

## Backend key ownership

- WSL is only a local client. It is not a source for employee enrollment keys.
- Enrollment must go through the key backend.
- The backend owns the employee-to-key mapping and key lifecycle.
- If `CLIPROXY_MANAGEMENT_KEY` is configured, the backend creates or rotates
  CLIProxyAPI client keys through the CLIProxyAPI management API.
- Do not delete existing CLIProxyAPI keys that were not created and tracked by
  the EzyHub backend.
- Generated CLIProxyAPI client key names should include the employee name or
  email prefix so the owner is visible directly inside CLIProxyAPI.
- The management key is an admin credential for the backend. It is not an
  employee model key and must not be written into Codex App config.

## OAuth and EzyHub pages

- Google account verification should reuse the existing EzyHub Google OAuth
  project/client when possible.
- The OAuth client must include the Codex enroll callback URI.
- The plugin should keep user-facing management and display pages in EzyHub
  rather than adding many local plugin pages.
- Long-lived employee keys must not appear in browser URLs or logs.

## MCP and knowledge base direction

- The future knowledge base and MCP management can live behind EzyHub.
- A facade means EzyHub can expose a stable, policy-controlled API or MCP surface
  to Codex while hiding internal service boundaries.
- Existing MCP entries that use `bearer_token_env_var`, such as MYOB MCP, can
  keep secrets in the Codex env file. This is separate from the model provider
  key, even when the same `CODEX_HOME/.env` file stores both variables.

## Codex App lifecycle

- Do not install `uv` or create a plugin `.venv` for enrollment. Codex App can
  use its bundled Python runtime to run plugin helper scripts.
- If system `python3` is unavailable on an employee machine, use the bundled
  Python path exposed by the Codex workspace runtime.
- After changing provider config, key material, plugin skills, or MCP tools,
  start a new Codex App thread.
- If the new thread still sees stale plugin or provider state, quit and reopen
  Codex App.
- Do not claim a change is active until the installed plugin cache and a new
  Codex App thread can see it.
