---
name: key-rotate
description: Rotate the current EzyHub CLIProxyAPI key and reconfigure Codex App.
---

# /key-rotate

Rotate the current company gateway key.

Run:

```bash
python3 plugins/ezyhub/scripts/ezyhub_backend.py key-rotate
```

The script calls the safer bearer-token endpoint:

```text
POST /keys/rotate Authorization: Bearer <current employee key>
```

After a successful rotation, it configures Codex with the new key through `codex login --with-api-key`.
Open a new Codex App thread after rotation. If the new thread is stale, quit and reopen Codex App.
