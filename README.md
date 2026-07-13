# EzyTrail Codex Plugins

Company plugin marketplace for [Codex App](https://openai.com/codex). Currently ships the **ezyhub** plugin: enroll your Codex App with the EzyTrail model gateway, sync company skills and MCP tools, and keep them updated automatically.

## Install (employees)

```bash
codex plugin marketplace add https://github.com/X-InsightAI/codex-plugins
codex plugin add ezyhub@ezytrail
```

Then in Codex App run `/enroll` — a browser opens to sign in with your company Google account and authorize the device. Everything else (gateway key, skills, MCP config, 4-hour auto-sync) is automatic. Open a new Codex thread afterwards.

## Update

```bash
codex plugin add ezyhub@ezytrail
```

(then open a new Codex thread)

## Notes

- Your gateway key is issued during `/enroll` and lives only on your device. This repository contains no secrets.
- Company skills are synced under the `ezyhub-` prefix and never touch your personal skills; an exact same-name skill is overwritten by the company version.
- Managed by the EzyTrail platform team. Source of truth for skills and backend lives in a private repository.
