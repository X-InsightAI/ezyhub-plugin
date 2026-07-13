---
name: sync-skills
description: Sync EzyHub role-based skills into the local Codex skills directory.
---

# /sync-skills

Sync company-managed role skills and the EzyHub KB MCP server config.

`/enroll` already runs this once as part of enrollment, and a background job installed by `/enroll` re-runs it automatically (default every 4 hours; see `plugins/ezyhub/skills/enroll/SKILL.md`). Run it manually to force an immediate refresh — for example after a role change or an announced skill update.

Run:

```bash
python3 plugins/ezyhub/scripts/ezyhub_backend.py sync-skills
```

The script calls:

```text
GET /skills Authorization: Bearer <employee key>
```

The response includes both role skills and the employee's MCP server list (`mcp_servers`). The helper writes both:

- skills to `CODEX_HOME/skills/<name>/SKILL.md`
- MCP servers as `[mcp_servers.<name>]` sections in `CODEX_HOME/config.toml`

Collision rules, tracked via the `.ezyhub-skills.json` manifest in `CODEX_HOME/skills/`:

- only names starting with the `ezyhub-` prefix are ever created, updated, or deleted by this sync
- on an exact name match, the server always wins: the local `ezyhub-*` skill or MCP section is overwritten with the server's current content
- a skill or MCP server previously synced but no longer returned by the backend is deleted (tracked via the manifest), so removed role skills don't linger
- personal (non-`ezyhub-*`) skills and MCP servers are never read, added, updated, or deleted by this sync

Open a new Codex App thread after syncing.
