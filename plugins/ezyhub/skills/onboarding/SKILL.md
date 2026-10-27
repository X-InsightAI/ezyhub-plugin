---
name: onboarding
description: Connect this computer's company Google Workspace (Gmail, Drive, Sheets, Docs, Calendar) and DingTalk (钉钉) accounts through the Google Workspace CLI (gws) and DingTalk Workspace CLI (dws), installing either CLI if it is missing. Use whenever an "[EzyHub onboarding incomplete]" reminder appears, or the user asks to connect, log in to, or reconnect Google, Gmail, or DingTalk.
---

# EzyHub onboarding

Every employee connects two company accounts once per computer:

1. **Google Workspace**: Gmail, Drive, Sheets, Docs, Calendar, in one sign-in.
2. **DingTalk (钉钉)**: docs, calendar, contacts, AI tables.

Until both work, a reminder appears at the start of every new Codex conversation.
Once a login is confirmed it is never checked or reminded about again.

All commands below use the onboarding helper next to this skill. `<plugin-root>` is the
directory two levels above this `SKILL.md` (the one containing `scripts/`). Use `python3`
on macOS and `python` on Windows. These commands need network access: if your shell
policy blocks one, ask the user to approve it.

```bash
python3 "<plugin-root>/scripts/ezyhub_onboarding.py" check
```

## Rules

- These are non-technical colleagues: short messages, one link per message, say exactly
  what to click. Reply in the user's language.
- Never paste tokens, token files, or the Google client configuration into the chat.
  The authorization link and the DingTalk code are fine to show.
- Only connect the user's **own** company accounts.

## Step 0: status

Run `check`. It prints `{"google": {"ok": ...}, "dingtalk": {"ok": ...}}`, with the reason
for anything not ok. Tell the user in one or two lines what is already connected and what is
left, then do the missing ones: Google first, then DingTalk.

## Step 1: Google Workspace

1. If the reason is that gws is not installed:

   ```bash
   python3 "<plugin-root>/scripts/ezyhub_onboarding.py" install-gws
   ```

2. Start the sign-in. It installs the company Google sign-in configuration (fetched from
   EzyHub, so the computer must already be enrolled; if it fails with 401, run `/enroll`
   first; with 404, tell the user the company Google connection is not ready yet and go on
   to DingTalk) and prints a line starting with `AUTHORIZATION LINK`:

   ```bash
   python3 "<plugin-root>/scripts/ezyhub_onboarding.py" google-login --start
   ```

   **Your next reply must contain that link** as a clickable markdown link on its own line,
   with these instructions:

   > 1. Open this link (a browser may already have opened it) and sign in with your
   >    **@ezytrail.com.au** account.
   > 2. Click "Allow". When the page says sign-in is complete, you can close it.

   The link only works on this computer, because Google sends the result back to it.

3. Then wait for the sign-in to finish (up to 10 minutes):

   ```bash
   python3 "<plugin-root>/scripts/ezyhub_onboarding.py" google-login --wait
   ```

If the user signs in with a personal Gmail instead of the company account, Google refuses
the company sign-in. Ask them to retry with the work account.

## Step 2: DingTalk

1. If the reason is that dws is not installed, install it with DingTalk's official installer:

   - macOS:
     ```bash
     curl -fsSL https://raw.githubusercontent.com/DingTalk-Real-AI/dingtalk-workspace-cli/main/scripts/install.sh | sh
     ```
   - Windows (PowerShell):
     ```powershell
     irm https://raw.githubusercontent.com/DingTalk-Real-AI/dingtalk-workspace-cli/main/scripts/install.ps1 | iex
     ```

2. Start the authorization. It prints an `AUTHORIZATION LINK` and an `AUTHORIZATION CODE`:

   ```bash
   python3 "<plugin-root>/scripts/ezyhub_onboarding.py" dingtalk-login --start
   ```

   **Your next reply must contain the link and the code**, for example:

   > 请在钉钉里打开这个链接完成授权（15 分钟内有效）：<link>
   > 授权码：<code>

3. Then wait for the approval (up to 10 minutes):

   ```bash
   python3 "<plugin-root>/scripts/ezyhub_onboarding.py" dingtalk-login --wait
   ```

If DingTalk says the organization has not enabled CLI access, the user must ask the
company's DingTalk admin to enable it, then run this step again.

## Step 3: confirm

Run `check` again. Both must be `ok`. Report the connected accounts, for example
"Google: jane@ezytrail.com.au, DingTalk: Jane", and ask the user to confirm they are theirs.
If one is not, disconnect it, then run `check --force` and redo that step:

- Google: `gws auth logout`
- DingTalk: `dws auth logout`

If `gws` or `dws` is not found by name, call it by full path: both live in `~/.local/bin`
(Windows: `C:\Users\<username>\.local\bin\gws.exe`, `dws.exe`).

The reminder stops on the next new conversation once both are connected.
