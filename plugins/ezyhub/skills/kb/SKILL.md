---
name: kb
description: Query the company knowledge base through the approved EzyHub MCP/FACADE server.
---

# /kb

Query company knowledge.

Current status: the plugin MCP endpoint is wired to the public KB/FACADE domain
`https://kb.ezyapis.com/mcp`.

Do not use IP addresses in plugin MCP URLs.

Expected production behavior:

- use the configured read-only EzyHub KB/FACADE MCP server (`ezyhub-kb`)
- respect server-side auth
- keep sensitive capability decisions on the server

Production credentials still need to be configured on the MCP service:

- `EZYHUB_API_KEY`
- `EZYHUB_WORKSPACE_ID`
- `EZYHUB_KNOWLEDGE_BASE_IDS`
- `EZYHUB_KB_MCP_BEARER_TOKEN`

Codex App must receive matching `EZYHUB_KB_MCP_TOKEN` in its process environment for bearer auth.
For local macOS Codex App testing, configure that launch environment with:

```bash
python3 plugins/ezyhub/scripts/ezyhub_backend.py configure-codex-app-kb-token
```

On Windows, use `python` instead of `python3` if that is the available launcher.

Then quit and reopen Codex App and start a new thread.
