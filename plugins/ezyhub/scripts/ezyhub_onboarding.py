#!/usr/bin/env python3
"""EzyHub onboarding: connect this computer's Google Workspace and DingTalk logins.

Google Workspace goes through the Google Workspace CLI (`gws`), DingTalk through
the DingTalk Workspace CLI (`dws`). Progress is kept in EZYHUB_HOME/onboarding-status.json;
a login that has been confirmed once is never checked or reminded about again.

Subcommands:
  status                       print the saved status (no checks)
  check [--force]              check the logins not yet confirmed (all with --force)
  session-start-hook           Codex SessionStart hook: remind until both are connected
  install-gws                  install the Google Workspace CLI into ~/.local/bin
  google-login --start|--wait  connect Google Workspace (company OAuth client)
  dingtalk-login --start|--wait  connect DingTalk (device authorization)
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable


STATUS_FILE = "onboarding-status.json"
RECHECK_SECONDS = 600
PROBE_TIMEOUT_SECONDS = 8
LOGIN_TIMEOUT_SECONDS = 600
GWS_SERVICES = "gmail,drive,sheets,docs,calendar"
GWS_RELEASES = "https://github.com/googleworkspace/cli/releases/latest/download"
ITEMS = ("google", "dingtalk")
LABELS = {
    "google": "Google Workspace (Gmail, Drive, Sheets, Docs, Calendar)",
    "dingtalk": "DingTalk (钉钉)",
}
GOOGLE_LINK_RE = re.compile(r"https://accounts\.google\.com/\S+")
DINGTALK_LINK_RE = re.compile(r"https://login\.dingtalk\.com/oauth2/device/verify\.htm\?user_code=[A-Za-z0-9-]+")


def ezyhub_home() -> Path:
    return Path(os.environ.get("EZYHUB_HOME", str(Path.home() / ".ezyhub"))).expanduser()


def local_bin() -> Path:
    return Path.home() / ".local" / "bin"


def load_backend_helper():
    script = Path(__file__).with_name("ezyhub_backend.py")
    spec = importlib.util.spec_from_file_location("_ezyhub_backend_for_onboarding", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- status -------------------------------------------------------------------


def status_path() -> Path:
    return ezyhub_home() / STATUS_FILE


def load_status() -> dict[str, Any]:
    try:
        data = json.loads(status_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_status(status: dict[str, Any]) -> None:
    path = status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def is_ok(status: dict[str, Any], item: str) -> bool:
    entry = status.get(item)
    return isinstance(entry, dict) and entry.get("ok") is True


def record(status: dict[str, Any], item: str, result: tuple[bool, str | None, str | None]) -> None:
    ok, account, error = result
    status[item] = {"ok": ok, "account": account, "error": error, "checked_at": time.time()}


# --- probes -------------------------------------------------------------------


def find_cli(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    # Fresh installs are not on this process's PATH yet, and hooks/scheduled
    # jobs run with a minimal PATH, so look in the usual install locations too.
    exe = f"{name}.exe" if os.name == "nt" else name
    candidates = [local_bin() / exe]
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            candidates.append(Path(appdata) / "npm" / f"{name}.cmd")
    else:
        candidates += [Path("/opt/homebrew/bin") / name, Path("/usr/local/bin") / name]
    return next((str(c) for c in candidates if c.is_file()), None)


def run_cli_json(args: list[str], timeout: float = PROBE_TIMEOUT_SECONDS) -> dict[str, Any]:
    result = subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        stdin=subprocess.DEVNULL,
    )
    text = result.stdout or ""
    start = text.find("{")  # gws may print a keyring notice before the JSON
    if start < 0:
        return {}
    try:
        data = json.loads(text[start:])
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def probe_google() -> tuple[bool, str | None, str | None]:
    gws = find_cli("gws")
    if gws is None:
        return False, None, "Google Workspace CLI (gws) is not installed"
    try:
        data = run_cli_json([gws, "gmail", "users", "getProfile", "--params", '{"userId": "me"}'])
    except Exception as exc:
        return False, None, f"gws failed: {exc}"
    email = data.get("emailAddress")
    if isinstance(email, str) and email:
        return True, email, None
    error = data.get("error")
    message = error.get("message") if isinstance(error, dict) else None
    return False, None, (message or "not logged in").splitlines()[0]


def probe_dingtalk() -> tuple[bool, str | None, str | None]:
    dws = find_cli("dws")
    if dws is None:
        return False, None, "DingTalk Workspace CLI (dws) is not installed"
    try:
        data = run_cli_json([dws, "auth", "status", "-f", "json"])
    except Exception as exc:
        return False, None, f"dws failed: {exc}"
    if data.get("authenticated") is True and data.get("refresh_token_valid", True) is not False:
        account = data.get("user_name") or data.get("corp_name")
        return True, account if isinstance(account, str) else None, None
    return False, None, "not logged in"


PROBES: dict[str, Callable[[], tuple[bool, str | None, str | None]]] = {
    "google": probe_google,
    "dingtalk": probe_dingtalk,
}


def _safe_probe(item: str) -> tuple[bool, str | None, str | None]:
    try:
        return PROBES[item]()
    except Exception as exc:
        return False, None, str(exc)


def refresh(status: dict[str, Any], *, force: bool = False, min_age: float = 0) -> dict[str, Any]:
    now = time.time()
    due = []
    for item in ITEMS:
        if is_ok(status, item) and not force:
            continue
        entry = status.get(item)
        checked_at = entry.get("checked_at") if isinstance(entry, dict) else None
        if not force and isinstance(checked_at, (int, float)) and now - checked_at < min_age:
            continue
        due.append(item)
    # In parallel: the session-start hook must stay well inside its timeout.
    with ThreadPoolExecutor(max_workers=len(ITEMS)) as pool:
        for item, result in zip(due, pool.map(_safe_probe, due)):
            record(status, item, result)
    return status


# --- session start hook ---------------------------------------------------------


def reminder_text(missing: list[str]) -> str:
    needed = "; ".join(LABELS[item] for item in missing)
    return (
        "[EzyHub onboarding incomplete] This computer still needs these company accounts connected: "
        f"{needed}. In your first reply of this conversation, briefly (one or two sentences, in the "
        "user's language) remind the user and offer to connect them now with the EzyHub `onboarding` "
        "skill; then help with whatever they asked. Do not repeat the reminder later in this conversation."
    )


def cmd_session_start_hook() -> None:
    try:
        sys.stdin.read()
    except Exception:
        pass
    status = load_status()
    if all(is_ok(status, item) for item in ITEMS):
        return
    status = refresh(status, min_age=RECHECK_SECONDS)
    save_status(status)
    missing = [item for item in ITEMS if not is_ok(status, item)]
    if missing:
        # ASCII-escaped: on Windows a piped stdout uses the ANSI code page, which
        # cannot encode the Chinese label.
        print(json.dumps({
            "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": reminder_text(missing)}
        }))


# --- installs -------------------------------------------------------------------


def gws_asset_name(sys_platform: str | None = None, machine: str | None = None) -> str:
    sys_platform = sys_platform or sys.platform
    machine = (machine or platform.machine()).lower()
    arch = "aarch64" if machine in ("arm64", "aarch64") else "x86_64"
    if sys_platform == "darwin":
        return f"google-workspace-cli-{arch}-apple-darwin.tar.gz"
    if sys_platform == "win32":
        return "google-workspace-cli-x86_64-pc-windows-msvc.zip"
    return f"google-workspace-cli-{arch}-unknown-linux-gnu.tar.gz"


def _download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "ezyhub-codex-plugin"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def _add_to_user_path_windows(directory: Path) -> bool:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
        try:
            current, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current, kind = "", winreg.REG_EXPAND_SZ
        entries = [e for e in current.split(";") if e]
        if any(os.path.normcase(os.path.expandvars(e)) == os.path.normcase(str(directory)) for e in entries):
            return False
        winreg.SetValueEx(key, "Path", 0, kind, ";".join([str(directory), *entries]))
    return True


def cmd_install_gws() -> None:
    existing = find_cli("gws")
    if existing:
        print(f"Google Workspace CLI already installed: {existing}")
        return
    asset = gws_asset_name()
    archive = _download(f"{GWS_RELEASES}/{asset}")
    expected = _download(f"{GWS_RELEASES}/{asset}.sha256").decode().split()[0].lower()
    if hashlib.sha256(archive).hexdigest() != expected:
        raise RuntimeError(f"checksum mismatch for {asset}; not installing")
    exe = "gws.exe" if os.name == "nt" else "gws"
    if asset.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            member = next((n for n in zf.namelist() if Path(n).name == exe), None)
            if member is None:
                raise RuntimeError(f"{exe} not found in {asset}")
            data = zf.read(member)
    else:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tf:
            member = next((m for m in tf.getmembers() if m.isfile() and Path(m.name).name == exe), None)
            extracted = tf.extractfile(member) if member is not None else None
            if extracted is None:
                raise RuntimeError(f"{exe} not found in {asset}")
            data = extracted.read()
    target = local_bin() / exe
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{exe}.tmp")
    tmp.write_bytes(data)
    tmp.chmod(0o755)
    os.replace(tmp, target)
    print(f"Installed Google Workspace CLI: {target}")
    if os.name == "nt" and _add_to_user_path_windows(target.parent):
        print(f"Added {target.parent} to your user PATH (new terminals pick it up).")


# --- logins ---------------------------------------------------------------------


def spawn_detached(args: list[str], log_path: Path) -> int:
    """Start a login that must outlive this command (it waits for the browser)."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as log:
        kwargs: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": log, "stderr": subprocess.STDOUT}
        if os.name == "nt":
            # CREATE_NO_WINDOW, not DETACHED_PROCESS: an npm .cmd shim would
            # otherwise pop up a console window for node.
            flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            try:
                return subprocess.Popen(args, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **kwargs).pid
            except OSError:  # the parent job may forbid breakaway
                return subprocess.Popen(args, creationflags=flags, **kwargs).pid
        return subprocess.Popen(args, start_new_session=True, **kwargs).pid


def wait_for_link(log_path: Path, pattern: re.Pattern[str], timeout: float = 20) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            match = pattern.search(log_path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            match = None
        if match:
            return match.group(0)
        time.sleep(0.5)
    tail = log_path.read_text(encoding="utf-8", errors="replace")[-600:] if log_path.exists() else ""
    raise RuntimeError(f"login did not print an authorization link in time. Output:\n{tail}")


def wait_until_connected(item: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while True:
        result = PROBES[item]()
        if result[0]:
            status = load_status()
            record(status, item, result)
            save_status(status)
            print(f"{LABELS[item]} connected" + (f": {result[1]}" if result[1] else "."))
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(f"{LABELS[item]} is not connected yet ({result[2]}). Start again with --start.")
        time.sleep(5)


def ensure_google_client(gws: str, backend_url: str) -> None:
    info = run_cli_json([gws, "auth", "status"])
    client_path = info.get("client_config")
    if not isinstance(client_path, str) or not client_path:
        raise RuntimeError("could not find where gws keeps its OAuth client (gws auth status)")
    helper = load_backend_helper()
    payload = helper.request_json(
        "GET", "/onboarding/google-client", backend_url=backend_url, token=helper.read_codex_key()
    )
    client = payload.get("client_config")
    if not isinstance(client, dict):
        raise RuntimeError("EzyHub did not return a Google client configuration")
    path = Path(client_path)
    content = json.dumps(client, indent=2) + "\n"
    # Only called once the Google check failed, so the company client wins over
    # whatever is there (e.g. a personal one); keep the old file recoverable.
    if path.exists():
        if path.read_text(encoding="utf-8", errors="replace") == content:
            return
        os.replace(path, path.with_name(path.name + ".before-ezyhub"))
    path.parent.mkdir(parents=True, exist_ok=True)
    old_umask = os.umask(0o177)
    try:
        path.write_text(content, encoding="utf-8")
    finally:
        os.umask(old_umask)
    print("Installed the company Google sign-in configuration for gws.")


def cmd_google_login(args: argparse.Namespace) -> None:
    if args.wait:
        wait_until_connected("google", args.timeout_seconds)
        return
    already = probe_google()
    if already[0]:
        status = load_status()
        record(status, "google", already)
        save_status(status)
        print(f"Google Workspace is already connected: {already[1]}")
        return
    gws = find_cli("gws")
    if gws is None:
        raise RuntimeError("Google Workspace CLI (gws) is not installed; run install-gws first")
    ensure_google_client(gws, args.backend_url)
    log_path = ezyhub_home() / "google-login.log"
    spawn_detached([gws, "auth", "login", "-s", GWS_SERVICES], log_path)
    link = wait_for_link(log_path, GOOGLE_LINK_RE)
    print(f"AUTHORIZATION LINK: {link}")
    print("Show this link to the user, then run: google-login --wait")


def cmd_dingtalk_login(args: argparse.Namespace) -> None:
    if args.wait:
        wait_until_connected("dingtalk", args.timeout_seconds)
        return
    already = probe_dingtalk()
    if already[0]:
        status = load_status()
        record(status, "dingtalk", already)
        save_status(status)
        print(f"DingTalk is already connected: {already[1]}")
        return
    dws = find_cli("dws")
    if dws is None:
        raise RuntimeError("DingTalk Workspace CLI (dws) is not installed")
    log_path = ezyhub_home() / "dingtalk-login.log"
    spawn_detached([dws, "auth", "login", "--device", "-f", "pretty"], log_path)
    link = wait_for_link(log_path, DINGTALK_LINK_RE)
    print(f"AUTHORIZATION LINK: {link}")
    code = link.rsplit("user_code=", 1)[-1]
    print(f"AUTHORIZATION CODE: {code}")
    print("Show this link to the user, then run: dingtalk-login --wait")


# --- cli ------------------------------------------------------------------------


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="EzyHub onboarding (Google Workspace + DingTalk)")
    parser.add_argument(
        "--backend-url", default=os.environ.get("EZYHUB_CODEX_BACKEND_URL", "https://codex.ezyapis.com")
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    check = sub.add_parser("check")
    check.add_argument("--force", action="store_true", help="re-check logins that were already confirmed")
    sub.add_parser("session-start-hook")
    sub.add_parser("install-gws")
    for name in ("google-login", "dingtalk-login"):
        login = sub.add_parser(name)
        mode = login.add_mutually_exclusive_group(required=True)
        mode.add_argument("--start", action="store_true", help="start the login and print the authorization link")
        mode.add_argument("--wait", action="store_true", help="wait until the user has approved the login")
        login.add_argument("--timeout-seconds", type=int, default=LOGIN_TIMEOUT_SECONDS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # account names may be non-Latin
    except (AttributeError, ValueError):
        pass
    if args.command == "session-start-hook":
        # Must never break or slow down a new Codex session.
        try:
            cmd_session_start_hook()
        except Exception:
            pass
        return 0
    try:
        if args.command == "status":
            print(json.dumps(load_status(), ensure_ascii=False))
        elif args.command == "check":
            status = refresh(load_status(), force=args.force)
            save_status(status)
            print(json.dumps(status, ensure_ascii=False))
        elif args.command == "install-gws":
            cmd_install_gws()
        elif args.command == "google-login":
            cmd_google_login(args)
        elif args.command == "dingtalk-login":
            cmd_dingtalk_login(args)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
