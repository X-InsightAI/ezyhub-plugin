# EzyHub Company Codex

This plugin configures Codex App to use the EzyHub company model gateway, company skills, and approved MCP tools.

Operational rules:

- Treat the CLIProxyAPI key as a secret. Never print it, paste it into chat, write it to logs, or put it in a browser URL.
- After `/enroll`, `/key-rotate`, plugin upgrade, MCP changes, or role skill sync, tell the user to open a new Codex App thread. If it still looks stale, ask them to quit and reopen Codex App.
- The company gateway is the hard boundary. Local skill files are distribution and guidance, not a security boundary.
- Prefer server-side MCP checks for sensitive capabilities.
