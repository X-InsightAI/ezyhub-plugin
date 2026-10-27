---
name: update
description: Update the EzyHub plugin on this computer and move older installs onto the current setup (background sync from Codex's plugin folder, company model list, onboarding reminder). Use when the user says "update EzyHub" or "更新 EzyHub", or when IT asks employees to update.
---

# Update EzyHub

`<plugin-root>` is the directory two levels above this `SKILL.md` (the one containing
`scripts/`). Use `python3` on macOS and `python` on Windows. The command needs network
access: if your shell policy blocks it, ask the user to approve it.

```bash
python3 "<plugin-root>/scripts/ezyhub_backend.py" update
```

It updates the plugin, syncs role skills, applies the current Codex settings, points the
background sync job at a launcher that always runs the newest plugin version, and removes
the old `~/.ezyhub/ezyhub-plugin` folder that earlier installs used. It is safe to run again.

Afterwards tell the user to **quit Codex App completely and reopen it** (on Windows:
right-click the app icon in the taskbar and choose "Exit"; closing the window is not
enough). After the restart, if Google or DingTalk is not connected yet, the next
conversation starts with a reminder and the `onboarding` skill takes it from there.
