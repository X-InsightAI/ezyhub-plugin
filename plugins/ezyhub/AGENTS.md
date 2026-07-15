# EzyHub Company Codex

This plugin connects Codex App to the EzyTrail company platform: the company model gateway plus role-based company skills (`/enroll`, `/key-status`, `/sync-skills`).

Operational rules:

- Treat the gateway key as a secret. Never print it, paste it into chat, write it to logs, or put it in a URL.
- Enrollment is mixed-auth: the user's own ChatGPT login stays. Never modify `auth.json` or sign them out.
- Never hand-edit `config.toml` — always go through the helper script, which backs up first and only touches EzyHub-owned sections.
- After `/enroll`, a plugin upgrade, or a skill sync, tell the user to quit and reopen Codex App — a full restart is the reliable way to pick up changes.
- The company gateway is the hard boundary. Local skill files are distribution and guidance, not a security boundary.
