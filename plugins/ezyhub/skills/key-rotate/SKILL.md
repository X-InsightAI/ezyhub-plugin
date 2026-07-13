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

After a successful rotation, it rewrites the mixed-auth Codex config: the new key
becomes the inline `experimental_bearer_token` on the retained provider id, with
`requires_openai_auth = true`, `image_generation = false`, and model `gpt-5.6-sol`.
The OpenAI/ChatGPT login in `auth.json` is left untouched.
Open a new Codex App thread after rotation. If the new thread is stale, quit and reopen Codex App. No reboot is required.
