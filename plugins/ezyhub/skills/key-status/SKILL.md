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
