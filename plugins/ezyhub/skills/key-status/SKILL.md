---
name: key-status
description: Show the current EzyHub Codex enrollment status and key metadata.
---

# /key-status

Show EzyHub Codex status.

Run:

```bash
python3 plugins/ezyhub/scripts/ezyhub_backend.py status
```

The script calls:

```text
GET /me Authorization: Bearer <employee key>
```

Show the employee email, role, key status, and recent usage summary. Do not print the key itself.

The helper reads the key locally from `EZYHUB_CODEX_KEY` (process env or
`CODEX_HOME/.env`) or from the inline `experimental_bearer_token` on the active
provider. Status is read-only: it does not change the mixed-auth config and
never touches the OpenAI/ChatGPT login in `auth.json`.
