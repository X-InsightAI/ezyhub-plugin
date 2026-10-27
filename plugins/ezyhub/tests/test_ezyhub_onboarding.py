from __future__ import annotations

import importlib.util
import io
import json
import subprocess
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load_onboarding():
    spec = importlib.util.spec_from_file_location("_test_ezyhub_onboarding", SCRIPTS / "ezyhub_onboarding.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def onboarding(tmp_path, monkeypatch):
    monkeypatch.setenv("EZYHUB_HOME", str(tmp_path / "ezyhub"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    return load_onboarding()


def fake_probes(onboarding, monkeypatch, results):
    calls: list[str] = []

    def make(item):
        def probe():
            calls.append(item)
            return results[item]
        return probe

    monkeypatch.setattr(onboarding, "PROBES", {item: make(item) for item in results})
    return calls


def run_hook(onboarding, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO('{"hook_event_name": "SessionStart", "source": "startup"}'))
    assert onboarding.main(["session-start-hook"]) == 0
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


def test_hook_is_silent_once_everything_is_connected(onboarding, monkeypatch, capsys):
    onboarding.save_status({
        "google": {"ok": True, "account": "a@ezytrail.com.au", "checked_at": 0},
        "dingtalk": {"ok": True, "account": "A", "checked_at": 0},
    })
    calls = fake_probes(onboarding, monkeypatch, {"google": (False, None, "x"), "dingtalk": (False, None, "x")})

    assert run_hook(onboarding, monkeypatch, capsys) is None
    assert calls == []  # confirmed logins are never re-checked


def test_hook_rechecks_missing_items_and_reminds_only_for_those(onboarding, monkeypatch, capsys):
    calls = fake_probes(onboarding, monkeypatch, {
        "google": (True, "jane@ezytrail.com.au", None),
        "dingtalk": (False, None, "dws not installed"),
    })

    output = run_hook(onboarding, monkeypatch, capsys)

    assert calls == ["google", "dingtalk"]
    context = output["hookSpecificOutput"]["additionalContext"]
    assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "DingTalk" in context
    assert "Google" not in context
    status = onboarding.load_status()
    assert status["google"]["ok"] is True
    assert status["google"]["account"] == "jane@ezytrail.com.au"
    assert status["dingtalk"]["ok"] is False


def test_hook_does_not_reprobe_within_recheck_window(onboarding, monkeypatch, capsys):
    now = 1_000_000.0
    monkeypatch.setattr(onboarding.time, "time", lambda: now)
    onboarding.save_status({
        "google": {"ok": False, "checked_at": now - 60},
        "dingtalk": {"ok": True, "checked_at": 0},
    })
    calls = fake_probes(onboarding, monkeypatch, {"google": (True, "x", None), "dingtalk": (True, "y", None)})

    output = run_hook(onboarding, monkeypatch, capsys)

    assert calls == []
    assert "Google" in output["hookSpecificOutput"]["additionalContext"]


def test_hook_never_fails_the_session(onboarding, monkeypatch, capsys):
    def boom():
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(onboarding, "PROBES", {"google": boom, "dingtalk": boom})
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert onboarding.main(["session-start-hook"]) == 0


def test_check_force_reprobes_confirmed_items(onboarding, monkeypatch, capsys):
    onboarding.save_status({"google": {"ok": True, "checked_at": 0}, "dingtalk": {"ok": True, "checked_at": 0}})
    calls = fake_probes(onboarding, monkeypatch, {"google": (True, "g", None), "dingtalk": (False, None, "logged out")})

    assert onboarding.main(["check"]) == 0
    assert calls == []
    assert onboarding.main(["check", "--force"]) == 0
    assert calls == ["google", "dingtalk"]
    printed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert printed["dingtalk"]["ok"] is False


def test_probe_google_reads_gmail_profile(onboarding, monkeypatch):
    monkeypatch.setattr(onboarding, "find_cli", lambda name: "/bin/gws")
    seen = []

    def fake_run(args, **kwargs):
        seen.append(args)
        return subprocess.CompletedProcess(args, 0, 'Using keyring backend: keyring\n{"emailAddress": "jane@ezytrail.com.au"}', "")

    monkeypatch.setattr(onboarding.subprocess, "run", fake_run)
    assert onboarding.probe_google() == (True, "jane@ezytrail.com.au", None)
    assert seen[0][:4] == ["/bin/gws", "gmail", "users", "getProfile"]

    monkeypatch.setattr(
        onboarding.subprocess, "run",
        lambda args, **kw: subprocess.CompletedProcess(args, 1, '{"error": {"code": 401, "message": "No credentials"}}', ""),
    )
    ok, account, error = onboarding.probe_google()
    assert (ok, account) == (False, None)
    assert "No credentials" in error

    monkeypatch.setattr(onboarding, "find_cli", lambda name: None)
    assert onboarding.probe_google()[0] is False


def test_probe_dingtalk_reads_auth_status(onboarding, monkeypatch):
    monkeypatch.setattr(onboarding, "find_cli", lambda name: "/bin/dws")
    monkeypatch.setattr(
        onboarding.subprocess, "run",
        lambda args, **kw: subprocess.CompletedProcess(args, 0, '{"authenticated": true, "user_name": "Jane"}', ""),
    )
    assert onboarding.probe_dingtalk() == (True, "Jane", None)

    monkeypatch.setattr(
        onboarding.subprocess, "run",
        lambda args, **kw: subprocess.CompletedProcess(args, 0, '{"success": true, "authenticated": false}', ""),
    )
    assert onboarding.probe_dingtalk()[0] is False


def test_login_links_are_extracted_from_cli_output(onboarding):
    google_log = "Open this URL in your browser to authenticate:\n\n  https://accounts.google.com/o/oauth2/auth?scope=a%20b&client_id=1\n"
    assert onboarding.GOOGLE_LINK_RE.search(google_log).group(0) == (
        "https://accounts.google.com/o/oauth2/auth?scope=a%20b&client_id=1"
    )
    dws_log = "visit https://login.dingtalk.com/oauth2/device/verify.htm?user_code=GHMK-SJZR\nauthorization code: GHMK-SJZR\n"
    assert onboarding.DINGTALK_LINK_RE.search(dws_log).group(0).endswith("user_code=GHMK-SJZR")


@pytest.mark.parametrize(
    ("platform", "machine", "expected"),
    [
        ("darwin", "arm64", "google-workspace-cli-aarch64-apple-darwin.tar.gz"),
        ("darwin", "x86_64", "google-workspace-cli-x86_64-apple-darwin.tar.gz"),
        ("win32", "AMD64", "google-workspace-cli-x86_64-pc-windows-msvc.zip"),
        ("linux", "x86_64", "google-workspace-cli-x86_64-unknown-linux-gnu.tar.gz"),
    ],
)
def test_gws_release_asset_name(onboarding, platform, machine, expected):
    assert onboarding.gws_asset_name(platform, machine) == expected


def test_hook_output_survives_non_utf8_windows_console(onboarding, tmp_path):
    import os
    import sys
    import time

    onboarding.save_status({
        "google": {"ok": True, "checked_at": 0},
        "dingtalk": {"ok": False, "checked_at": time.time()},  # fresh: no probe
    })
    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "ezyhub_onboarding.py"), "session-start-hook"],
        input="{}", capture_output=True, text=True, env=env, check=True,
    )
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "DingTalk (钉钉)" in context


def test_company_google_client_replaces_other_client(onboarding, monkeypatch, tmp_path):
    client_path = tmp_path / "gws" / "client_secret.json"
    client_path.parent.mkdir()
    client_path.write_text('{"installed": {"client_id": "personal"}}', encoding="utf-8")
    monkeypatch.setattr(onboarding, "run_cli_json", lambda args, **kw: {
        "client_config": str(client_path), "client_config_exists": True,
    })

    class Helper:
        @staticmethod
        def read_codex_key():
            return "sk-ezyhub-key"

        @staticmethod
        def request_json(method, path, *, backend_url, token):
            assert (method, path, token) == ("GET", "/onboarding/google-client", "sk-ezyhub-key")
            return {"client_config": {"installed": {"client_id": "company"}}}

    monkeypatch.setattr(onboarding, "load_backend_helper", lambda: Helper)
    onboarding.ensure_google_client("/bin/gws", "https://b")

    assert json.loads(client_path.read_text())["installed"]["client_id"] == "company"
    assert "personal" in (client_path.parent / "client_secret.json.before-ezyhub").read_text()
