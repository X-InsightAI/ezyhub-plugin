from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(f"_test_{name}", SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def homes(tmp_path, monkeypatch):
    codex = tmp_path / "codex"
    ezy = tmp_path / "ezyhub"
    codex.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex))
    monkeypatch.setenv("EZYHUB_HOME", str(ezy))
    return codex, ezy


def install_fake_plugin(codex: Path, version: str, marker: str) -> Path:
    scripts = codex / "plugins" / "cache" / "ezytrail" / "ezyhub" / version / "scripts"
    scripts.mkdir(parents=True)
    for name in ("ezyhub_backend.py", "ezyhub_onboarding.py"):
        (scripts / name).write_text(
            f"import sys\nprint({marker!r}, {name!r}, sys.argv[1:])\n", encoding="utf-8"
        )
    return scripts


# --- hook trust -------------------------------------------------------------


def test_trusted_hash_matches_codex_for_known_hook():
    helper = load_script("ezyhub_backend")
    group = {
        "matcher": "startup|resume|clear|compact",
        "hooks": [
            {
                "type": "command",
                "command": 'echo "Code discovery: prefer codebase-memory-mcp (search_graph, trace_path, '
                'get_code_snippet, query_graph, search_code) over grep/file-read; run index_repository '
                'first if the project is not indexed."',
            }
        ],
    }
    # Value Codex itself wrote to config.toml after the user trusted this hook.
    assert helper.codex_hook_trusted_hash("session_start", group) == (
        "sha256:ed997f3f9766959ba56dc2243cd02468d6eadad80d92b7f5a650e6fc0fb0c050"
    )


def test_install_onboarding_hook_writes_hook_and_trust(homes):
    helper = load_script("ezyhub_backend")
    codex, ezy = homes
    (codex / "config.toml").write_text('model_provider = "ezyhub"\n\n[features]\nweb_search = true\n', encoding="utf-8")
    launcher = ezy / "ezyhub_run.py"

    helper.install_onboarding_hook(launcher)

    hooks = json.loads((codex / "hooks.json").read_text(encoding="utf-8"))
    group = hooks["hooks"]["SessionStart"][0]
    assert group["matcher"] == "startup"
    assert str(launcher) in group["hooks"][0]["command"]
    assert "ezyhub_onboarding.py session-start-hook" in group["hooks"][0]["command"]
    config = tomllib.loads((codex / "config.toml").read_text(encoding="utf-8"))
    key = f"{codex / 'hooks.json'}:session_start:0:0"
    assert config["hooks"]["state"][key]["trusted_hash"] == helper.codex_hook_trusted_hash("session_start", group)
    assert config["hooks"]["state"][key]["enabled"] is True
    assert config["features"]["web_search"] is True


def test_install_onboarding_hook_is_idempotent_and_keeps_other_hooks(homes):
    helper = load_script("ezyhub_backend")
    codex, ezy = homes
    other = {"hooks": [{"type": "command", "command": "echo mine"}]}
    (codex / "hooks.json").write_text(
        json.dumps({"hooks": {"SessionStart": [other], "Stop": [other]}}), encoding="utf-8"
    )
    (codex / "config.toml").write_text(
        'model_provider = "ezyhub"\n\n[hooks.state."/x/hooks.json:stop:0:0"]\ntrusted_hash = "sha256:keep"\n',
        encoding="utf-8",
    )
    launcher = ezy / "ezyhub_run.py"

    helper.install_onboarding_hook(launcher)
    first = (codex / "config.toml").read_text(encoding="utf-8")
    helper.install_onboarding_hook(launcher)
    second = (codex / "config.toml").read_text(encoding="utf-8")

    assert first == second
    hooks = json.loads((codex / "hooks.json").read_text(encoding="utf-8"))
    assert hooks["hooks"]["SessionStart"][0] == other
    assert hooks["hooks"]["Stop"] == [other]
    assert len(hooks["hooks"]["SessionStart"]) == 2
    config = tomllib.loads(second)
    assert f"{codex / 'hooks.json'}:session_start:1:0" in config["hooks"]["state"]
    assert config["hooks"]["state"]["/x/hooks.json:stop:0:0"]["trusted_hash"] == "sha256:keep"
    assert second.count(":session_start:1:0") == 1


def test_install_onboarding_hook_refuses_to_break_config(homes):
    helper = load_script("ezyhub_backend")
    codex, ezy = homes
    original = '[hooks]\nstate = { "a" = { trusted_hash = "x" } }\n'
    (codex / "config.toml").write_text(original, encoding="utf-8")

    with pytest.raises(RuntimeError):
        helper.install_onboarding_hook(ezy / "ezyhub_run.py")

    assert (codex / "config.toml").read_text(encoding="utf-8") == original


def test_quoted_hook_state_headers_do_not_confuse_key_lookup(homes):
    helper = load_script("ezyhub_backend")
    codex, ezy = homes
    (codex / ".env").write_text("EZYHUB_CODEX_KEY=sk-ezyhub-env\n", encoding="utf-8")
    (codex / "config.toml").write_text(
        'model_provider = "ezyhub"\n\n[model_providers.ezyhub]\nname = "EzyHub"\n'
        'base_url = "https://api.ezyapis.com/v1"\n',
        encoding="utf-8",
    )
    helper.install_onboarding_hook(ezy / "ezyhub_run.py")

    parsed = helper.parse_codex_config_strings((codex / "config.toml").read_text(encoding="utf-8"))
    assert parsed["model_provider"] == "ezyhub"
    assert helper.read_codex_key() == "sk-ezyhub-env"


# --- launcher ---------------------------------------------------------------


def test_launcher_runs_newest_plugin_version(homes):
    helper = load_script("ezyhub_backend")
    codex, _ = homes
    install_fake_plugin(codex, "1.0.9", "old")
    install_fake_plugin(codex, "1.0.10", "new")
    launcher = helper.write_launcher()

    out = subprocess.run(
        [sys.executable, str(launcher), "--backend-url", "https://b", "sync-skills"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert out.strip() == "new ezyhub_backend.py ['--backend-url', 'https://b', 'sync-skills']"

    out = subprocess.run(
        [sys.executable, str(launcher), "ezyhub_onboarding.py", "session-start-hook"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert out.strip() == "new ezyhub_onboarding.py ['session-start-hook']"


def test_auto_sync_uses_launcher_once_plugin_is_installed(homes, monkeypatch):
    helper = load_script("ezyhub_backend")
    codex, ezy = homes
    calls: list = []
    monkeypatch.setattr(helper, "current_platform", lambda: "win32")
    monkeypatch.setattr(helper.subprocess, "run", lambda args, **kw: calls.append(args))

    helper.install_auto_sync(4, "https://b")
    assert str(Path(helper.__file__).resolve()) in calls[-1][5]  # dev checkout: no cache yet

    install_fake_plugin(codex, "1.0.14", "x")
    helper.install_auto_sync(4, "https://b")
    assert calls[-1] == helper.build_schtasks_create_args(4, str(ezy / "ezyhub_run.py"), "https://b")
    assert (ezy / "ezyhub_run.py").exists()


# --- config migration -------------------------------------------------------


def test_ensure_model_discovery_patches_only_ezyhub_provider(homes):
    configure = load_script("ezyhub_configure_codex")
    codex, _ = homes
    config = codex / "config.toml"
    config.write_text(
        'model_provider = "work"\nmodel = "my-choice"\n\n[model_providers.work]\nname = "EzyHub"\n'
        'base_url = "https://api.ezyapis.com/v1"\nexperimental_bearer_token = "sk-ezyhub-x"\n\n'
        '[model_providers.other]\nname = "Other"\nbase_url = "https://other/v1"\n',
        encoding="utf-8",
    )

    assert configure.ensure_model_discovery(codex) is True
    assert configure.ensure_model_discovery(codex) is False  # idempotent

    parsed = tomllib.loads(config.read_text(encoding="utf-8"))
    assert parsed["model"] == "my-choice"  # never resets the user's model
    assert parsed["features"]["api_key_model_discovery"] is True
    assert parsed["model_providers"]["work"]["model_catalog_url"] == "https://api.ezyapis.com/v1/models"
    assert "model_catalog_url" not in parsed["model_providers"]["other"]


def test_ensure_model_discovery_leaves_foreign_provider_alone(homes):
    configure = load_script("ezyhub_configure_codex")
    codex, _ = homes
    original = 'model_provider = "openai"\n'
    (codex / "config.toml").write_text(original, encoding="utf-8")

    assert configure.ensure_model_discovery(codex) is False
    assert (codex / "config.toml").read_text(encoding="utf-8") == original


# --- self update ------------------------------------------------------------


def test_self_update_upgrades_marketplace_and_never_raises(monkeypatch):
    helper = load_script("ezyhub_backend")
    calls: list = []
    monkeypatch.setattr(helper, "find_codex_cli", lambda: "/bin/codex")

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "Upgraded marketplace `ezytrail`", "")

    monkeypatch.setattr(helper.subprocess, "run", fake_run)
    assert "Upgraded" in helper.self_update()
    assert calls == [["/bin/codex", "plugin", "marketplace", "upgrade", "ezytrail"]]

    monkeypatch.setattr(helper.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    assert "boom" in helper.self_update()

    monkeypatch.setattr(helper, "find_codex_cli", lambda: None)
    assert "not found" in helper.self_update()


def test_onboarding_hook_status_detects_trust_drift(homes):
    helper = load_script("ezyhub_backend")
    codex, ezy = homes
    assert helper.onboarding_hook_status()["installed"] is False

    helper.install_onboarding_hook(ezy / "ezyhub_run.py")
    assert helper.onboarding_hook_status() == {"installed": True, "trusted": True}

    config = codex / "config.toml"
    config.write_text(config.read_text(encoding="utf-8").replace("sha256:", "sha256:0"), encoding="utf-8")
    assert helper.onboarding_hook_status() == {"installed": True, "trusted": False}


def test_remove_legacy_clone_only_after_plugin_is_installed(homes):
    helper = load_script("ezyhub_backend")
    codex, ezy = homes
    clone = ezy / "ezyhub-plugin"
    (clone / ".git" / "objects").mkdir(parents=True)
    readonly = clone / ".git" / "objects" / "pack.idx"
    readonly.write_text("x", encoding="utf-8")
    readonly.chmod(0o444)
    script = clone / "plugins" / "ezyhub" / "scripts" / "ezyhub_backend.py"
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")

    assert helper.remove_legacy_clone() is None  # plugin not installed: background sync still needs it
    assert clone.exists()

    install_fake_plugin(codex, "1.0.14", "x")
    assert "Removed" in helper.remove_legacy_clone()
    assert not clone.exists()
    assert helper.remove_legacy_clone() is None


def test_config_parser_keeps_keys_after_multiline_arrays():
    helper = load_script("ezyhub_backend")
    parsed = helper.parse_codex_config_strings(
        'model_provider = "ezyhub"\n[mcp_servers.x]\nargs = [\n  "a",\n]\nurl = "http://x"\n'
        '[hooks.state."C:\\\\x\\\\hooks.json:session_start:0:0"]\ntrusted_hash = "sha256:1"\n'
    )
    assert parsed["mcp_servers"]["x"]["url"] == "http://x"
    assert "trusted_hash" not in parsed["mcp_servers"]["x"]
