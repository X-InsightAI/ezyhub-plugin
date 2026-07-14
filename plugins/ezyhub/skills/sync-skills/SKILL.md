---
name: sync-skills
description: Sync EzyHub role-based skills into the local Codex skills directory.
---

# /sync-skills

Sync company-managed role skills.

`/enroll` already runs this once as part of enrollment, and a background job installed by `/enroll` re-runs it automatically (default every 4 hours; see `plugins/ezyhub/skills/enroll/SKILL.md`). Run it manually to force an immediate refresh — for example after a role change or an announced skill update.

Run:

```bash
python3 plugins/ezyhub/scripts/ezyhub_backend.py sync-skills
```

The script calls:

```text
GET /skills Authorization: Bearer <employee key>
```

The response includes both role skills and the employee's MCP server list (`mcp_servers`). Skills are full directory trees — `SKILL.md` plus any `scripts/`, `references/`, and `assets/` files (binary files supported) — not just a single `SKILL.md`. The helper writes both:

- each skill's complete tree to `CODEX_HOME/skills/<name>/`
- MCP servers as `[mcp_servers.<name>]` sections in `CODEX_HOME/config.toml`

Collision rules are file-level, tracked via the `.ezyhub-skills.json` manifest in `CODEX_HOME/skills/`:

- only names starting with the `ezyhub-` prefix are ever created, updated, or deleted by this sync
- within a managed `ezyhub-*` skill dir, a server file overwrites the local file at the same path (on a path collision, the server version wins)
- only files the manifest recorded as server-managed are ever deleted (when the server no longer serves them, or the whole skill is removed); emptied directories are then pruned
- files the employee added at other paths inside a managed skill dir, and personal (non-`ezyhub-*`) skills and MCP servers, are never read, added, updated, or deleted by this sync
- the sync refuses to write or delete through symlinks, or anywhere outside the managed `ezyhub-*` skill dir; if a managed skill dir is itself a symlink, the sync skips that skill entirely with a warning

Open a new Codex App thread after syncing.

## Publish a skill

To submit a local skill to the company library for your role:

```bash
python3 plugins/ezyhub/scripts/ezyhub_backend.py publish-skill <local-skill-dir> --role <role>
```

This uploads the skill directory (must contain a `SKILL.md`; symlinks are rejected) to a pending area on the backend. The skill name defaults to the directory name and must start with `ezyhub-`; pass `--name ezyhub-<slug>` to override. An admin reviews and approves the submission before it appears in the company library and reaches other employees via sync.
