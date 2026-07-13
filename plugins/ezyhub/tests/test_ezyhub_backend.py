from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import sys
from pathlib import Path


def load_helper():
    script = Path(__file__).resolve().parents[1] / "scripts" / "ezyhub_backend.py"
    spec = importlib.util.spec_from_file_location("ezyhub_backend_helper", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_configure_helper():
    script = Path(__file__).resolve().parents[1] / "scripts" / "ezyhub_configure_codex.py"
    spec = importlib.util.spec_from_file_location("ezyhub_configure_codex_helper", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def skill_content(name: str, body: str) -> str:
    return f"---\nname: {name}\ndescription: Test skill.\n---\n\n# {name}\n\n{body}\n"


def skill_files(name: str, body: str, extra: list[dict] | None = None) -> list[dict]:
    files = [{"path": "SKILL.md", "content": skill_content(name, body), "encoding": "utf-8"}]
    files.extend(extra or [])
    return files


def test_sync_skills_is_idempotent_and_removes_only_managed_skills(tmp_path, monkeypatch):
    helper = load_helper()
    codex_home = tmp_path / "codex-home"
    skills_dir = codex_home / "skills"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    old_skill = skills_dir / "ezyhub-old" / "SKILL.md"
    old_skill.parent.mkdir(parents=True)
    old_skill.write_text(skill_content("ezyhub-old", "remove me"), encoding="utf-8")

    personal_skill = skills_dir / "personal-tool" / "SKILL.md"
    personal_skill.parent.mkdir(parents=True)
    personal_skill.write_text(skill_content("personal-tool", "keep me"), encoding="utf-8")

    unmanaged_company_skill = skills_dir / "ezyhub-unmanaged" / "SKILL.md"
    unmanaged_company_skill.parent.mkdir(parents=True)
    unmanaged_company_skill.write_text(skill_content("ezyhub-unmanaged", "keep me too"), encoding="utf-8")

    helper.write_manifest(skills_dir, ["ezyhub-current", "ezyhub-old"])

    payload = {
        "role": "engineering",
        "skills": [
            {
                "name": "ezyhub-current",
                "files": skill_files("ezyhub-current", "current body"),
            },
            {
                "name": "ezyhub-extra",
                "files": skill_files("ezyhub-extra", "extra body"),
            },
        ],
    }

    monkeypatch.setattr(helper, "read_codex_key", lambda: "sk-test")
    monkeypatch.setattr(
        helper,
        "request_json",
        lambda method, path, **kwargs: payload,
    )
    args = argparse.Namespace(backend_url="http://backend")

    helper.cmd_sync_skills(args)
    helper.cmd_sync_skills(args)

    assert not old_skill.exists()
    assert personal_skill.exists()
    assert unmanaged_company_skill.exists()
    assert (skills_dir / "ezyhub-current" / "SKILL.md").read_text(encoding="utf-8") == skill_content(
        "ezyhub-current",
        "current body",
    )
    assert (skills_dir / "ezyhub-extra" / "SKILL.md").read_text(encoding="utf-8") == skill_content(
        "ezyhub-extra",
        "extra body",
    )

    manifest = json.loads((skills_dir / helper.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest == {
        "managed": ["ezyhub-current", "ezyhub-extra"],
        "mcp_servers": [],
        "managed_files": {"ezyhub-current": ["SKILL.md"], "ezyhub-extra": ["SKILL.md"]},
    }


def test_sync_skills_rejects_non_company_skill_names(tmp_path, monkeypatch):
    helper = load_helper()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setattr(helper, "read_codex_key", lambda: "sk-test")
    monkeypatch.setattr(
        helper,
        "request_json",
        lambda method, path, **kwargs: {
            "role": "engineering",
            "skills": [{"name": "outside", "files": skill_files("outside", "bad")}],
        },
    )

    try:
        helper.cmd_sync_skills(argparse.Namespace(backend_url="http://backend"))
    except RuntimeError as exc:
        assert "refusing to write non-company skill name" in str(exc)
    else:
        raise AssertionError("cmd_sync_skills should reject non-company skills")


def _sync_with_payload(helper, monkeypatch, payload):
    monkeypatch.setattr(helper, "read_codex_key", lambda: "sk-test")
    monkeypatch.setattr(helper, "request_json", lambda method, path, **kwargs: payload)
    helper.cmd_sync_skills(argparse.Namespace(backend_url="http://backend"))


def test_sync_skills_writes_multi_file_tree(tmp_path, monkeypatch):
    helper = load_helper()
    codex_home = tmp_path / "codex-home"
    skills_dir = codex_home / "skills"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    payload = {
        "role": "engineering",
        "skills": [
            {
                "name": "ezyhub-x",
                "files": skill_files(
                    "ezyhub-x",
                    "body",
                    extra=[{"path": "scripts/run.py", "content": "print('hi')\n", "encoding": "utf-8"}],
                ),
            },
        ],
    }
    _sync_with_payload(helper, monkeypatch, payload)

    assert (skills_dir / "ezyhub-x" / "SKILL.md").read_text(encoding="utf-8") == skill_content("ezyhub-x", "body")
    assert (skills_dir / "ezyhub-x" / "scripts" / "run.py").read_text(encoding="utf-8") == "print('hi')\n"
    manifest = helper.load_manifest(skills_dir)
    assert manifest["managed_files"] == {"ezyhub-x": ["SKILL.md", "scripts/run.py"]}


def test_sync_skills_deletes_server_removed_files_but_keeps_personal_files(tmp_path, monkeypatch):
    helper = load_helper()
    codex_home = tmp_path / "codex-home"
    skills_dir = codex_home / "skills"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    extra = [{"path": "scripts/run.py", "content": "print('hi')\n", "encoding": "utf-8"}]
    payload = {
        "role": "engineering",
        "skills": [{"name": "ezyhub-x", "files": skill_files("ezyhub-x", "body", extra=extra)}],
    }
    _sync_with_payload(helper, monkeypatch, payload)

    # employee adds a personal file under a different dir inside the managed skill
    personal = skills_dir / "ezyhub-x" / "notes" / "mine.md"
    personal.parent.mkdir(parents=True)
    personal.write_text("my notes\n", encoding="utf-8")

    # server drops scripts/run.py on the next sync
    payload = {
        "role": "engineering",
        "skills": [{"name": "ezyhub-x", "files": skill_files("ezyhub-x", "body v2")}],
    }
    _sync_with_payload(helper, monkeypatch, payload)

    assert not (skills_dir / "ezyhub-x" / "scripts" / "run.py").exists()
    assert not (skills_dir / "ezyhub-x" / "scripts").exists()  # emptied managed dir pruned
    assert personal.read_text(encoding="utf-8") == "my notes\n"
    manifest = helper.load_manifest(skills_dir)
    assert manifest["managed_files"] == {"ezyhub-x": ["SKILL.md"]}


def test_sync_skills_refuses_to_write_through_symlinked_dir(tmp_path, monkeypatch):
    helper = load_helper()
    codex_home = tmp_path / "codex-home"
    skills_dir = codex_home / "skills"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    # employee has an unrelated project dir with a file the sync must never touch
    outside = tmp_path / "project"
    outside.mkdir()
    victim = outside / "run.py"
    victim.write_text("mine\n", encoding="utf-8")

    # first sync creates the skill, then employee turns scripts/ into a symlink to it
    _sync_with_payload(
        helper,
        monkeypatch,
        {"role": "engineering", "skills": [{"name": "ezyhub-x", "files": skill_files("ezyhub-x", "body")}]},
    )
    (skills_dir / "ezyhub-x" / "scripts").symlink_to(outside, target_is_directory=True)

    extra = [{"path": "scripts/run.py", "content": "print('hi')\n", "encoding": "utf-8"}]
    payload = {
        "role": "engineering",
        "skills": [{"name": "ezyhub-x", "files": skill_files("ezyhub-x", "body", extra=extra)}],
    }
    try:
        _sync_with_payload(helper, monkeypatch, payload)
    except (RuntimeError, ValueError):
        pass
    # the outside file must never be written through the symlink
    assert victim.read_text(encoding="utf-8") == "mine\n"


def test_sync_skills_refuses_to_delete_through_symlinked_dir(tmp_path, monkeypatch):
    helper = load_helper()
    codex_home = tmp_path / "codex-home"
    skills_dir = codex_home / "skills"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    outside = tmp_path / "project"
    outside.mkdir()
    victim = outside / "run.py"
    victim.write_text("mine\n", encoding="utf-8")

    # manifest tracks scripts/run.py, but scripts is now a symlink to the outside dir
    skill_dir = skills_dir / "ezyhub-x"
    (skill_dir).mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(skill_content("ezyhub-x", "body"), encoding="utf-8")
    (skill_dir / "scripts").symlink_to(outside, target_is_directory=True)
    helper.write_manifest(
        skills_dir,
        ["ezyhub-x"],
        [],
        {"ezyhub-x": ["SKILL.md", "scripts/run.py"]},
    )

    # next sync drops scripts/run.py from the bundle -> removal would target the symlink path
    payload = {
        "role": "engineering",
        "skills": [{"name": "ezyhub-x", "files": skill_files("ezyhub-x", "body")}],
    }
    try:
        _sync_with_payload(helper, monkeypatch, payload)
    except (RuntimeError, ValueError):
        pass
    assert victim.exists()
    assert victim.read_text(encoding="utf-8") == "mine\n"


def test_sync_skills_skips_write_when_top_level_skill_dir_is_symlink(tmp_path, monkeypatch, capsys):
    helper = load_helper()
    codex_home = tmp_path / "codex-home"
    skills_dir = codex_home / "skills"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    # employee points the whole managed skill dir at an outside checkout
    outside = tmp_path / "checkout"
    outside.mkdir()
    victim = outside / "SKILL.md"
    victim.write_text("mine\n", encoding="utf-8")
    skills_dir.mkdir(parents=True)
    (skills_dir / "ezyhub-x").symlink_to(outside, target_is_directory=True)

    payload = {
        "role": "engineering",
        "skills": [{"name": "ezyhub-x", "files": skill_files("ezyhub-x", "server body")}],
    }
    _sync_with_payload(helper, monkeypatch, payload)

    # nothing written through the symlinked top-level dir; symlink itself untouched
    assert victim.read_text(encoding="utf-8") == "mine\n"
    assert (skills_dir / "ezyhub-x").is_symlink()
    out = capsys.readouterr().out
    assert "warning: skills/ezyhub-x is a symlink" in out
    manifest = helper.load_manifest(skills_dir)
    assert "ezyhub-x" not in manifest["managed"]


def test_sync_skills_skips_removal_when_top_level_skill_dir_is_symlink(tmp_path, monkeypatch, capsys):
    helper = load_helper()
    codex_home = tmp_path / "codex-home"
    skills_dir = codex_home / "skills"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    outside = tmp_path / "checkout"
    outside.mkdir()
    victim = outside / "SKILL.md"
    victim.write_text("mine\n", encoding="utf-8")
    skills_dir.mkdir(parents=True)
    (skills_dir / "ezyhub-x").symlink_to(outside, target_is_directory=True)

    # manifest claims ezyhub-x (and its SKILL.md) is managed; server no longer serves it
    helper.write_manifest(skills_dir, ["ezyhub-x"], [], {"ezyhub-x": ["SKILL.md"]})
    _sync_with_payload(helper, monkeypatch, {"role": "engineering", "skills": []})

    # outside files never unlinked through the symlink; symlink itself not deleted
    assert victim.exists()
    assert victim.read_text(encoding="utf-8") == "mine\n"
    assert (skills_dir / "ezyhub-x").is_symlink()
    out = capsys.readouterr().out
    assert "warning: skills/ezyhub-x is a symlink" in out


def test_sync_skills_binary_asset_round_trips(tmp_path, monkeypatch):
    helper = load_helper()
    codex_home = tmp_path / "codex-home"
    skills_dir = codex_home / "skills"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    blob = b"\x89PNG\r\n\x1a\n\x00\x01\x02\xff"
    extra = [{"path": "assets/logo.png", "content": base64.b64encode(blob).decode("ascii"), "encoding": "base64"}]
    payload = {
        "role": "engineering",
        "skills": [{"name": "ezyhub-x", "files": skill_files("ezyhub-x", "body", extra=extra)}],
    }
    _sync_with_payload(helper, monkeypatch, payload)

    assert (skills_dir / "ezyhub-x" / "assets" / "logo.png").read_bytes() == blob


def test_sync_skills_rejects_path_traversal_in_bundle(tmp_path, monkeypatch):
    helper = load_helper()
    codex_home = tmp_path / "codex-home"
    skills_dir = codex_home / "skills"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    extra = [{"path": "../evil.md", "content": "pwned\n", "encoding": "utf-8"}]
    payload = {
        "role": "engineering",
        "skills": [{"name": "ezyhub-x", "files": skill_files("ezyhub-x", "body", extra=extra)}],
    }
    try:
        _sync_with_payload(helper, monkeypatch, payload)
    except (RuntimeError, ValueError):
        pass
    else:
        raise AssertionError("cmd_sync_skills should reject traversal paths")
    assert not (skills_dir / "evil.md").exists()
    # nothing is written for the offending skill (validation happens before any write)
    assert not (skills_dir / "ezyhub-x" / "SKILL.md").exists()


def test_sync_skills_rejects_missing_or_frontmatterless_skill_md(tmp_path, monkeypatch):
    helper = load_helper()
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    for files in (
        [{"path": "README.md", "content": "no skill\n", "encoding": "utf-8"}],
        [{"path": "SKILL.md", "content": "# no frontmatter\n", "encoding": "utf-8"}],
    ):
        payload = {"role": "engineering", "skills": [{"name": "ezyhub-x", "files": files}]}
        try:
            _sync_with_payload(helper, monkeypatch, payload)
        except RuntimeError as exc:
            assert "SKILL.md" in str(exc) or "frontmatter" in str(exc)
        else:
            raise AssertionError("cmd_sync_skills should reject invalid SKILL.md")


def test_sync_skills_removes_skill_tracked_by_old_format_manifest(tmp_path, monkeypatch):
    helper = load_helper()
    codex_home = tmp_path / "codex-home"
    skills_dir = codex_home / "skills"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    old_skill = skills_dir / "ezyhub-old" / "SKILL.md"
    old_skill.parent.mkdir(parents=True)
    old_skill.write_text(skill_content("ezyhub-old", "remove me"), encoding="utf-8")
    # old-format manifest: no managed_files key
    (skills_dir / helper.MANIFEST_NAME).write_text('{"managed": ["ezyhub-old"]}', encoding="utf-8")

    payload = {"role": "engineering", "skills": []}
    _sync_with_payload(helper, monkeypatch, payload)

    assert not old_skill.exists()
    assert not old_skill.parent.exists()


def test_manifest_v3_roundtrip_and_legacy_tolerance(tmp_path):
    helper = load_helper()
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    # v1 legacy shape: managed_files defaults to per-skill empty lists
    (skills_dir / ".ezyhub-skills.json").write_text('{"managed": ["ezyhub-a"]}')
    manifest = helper.load_manifest(skills_dir)
    assert manifest == {"managed": ["ezyhub-a"], "mcp_servers": [], "managed_files": {"ezyhub-a": []}}
    # v2 shape (managed + mcp_servers, no managed_files)
    (skills_dir / ".ezyhub-skills.json").write_text('{"managed": ["ezyhub-a"], "mcp_servers": ["ezyhub-kb"]}')
    manifest = helper.load_manifest(skills_dir)
    assert manifest == {
        "managed": ["ezyhub-a"],
        "mcp_servers": ["ezyhub-kb"],
        "managed_files": {"ezyhub-a": []},
    }
    # v3 roundtrip
    helper.write_manifest(skills_dir, ["ezyhub-a"], ["ezyhub-kb"], {"ezyhub-a": ["SKILL.md", "scripts/run.py"]})
    assert helper.load_manifest(skills_dir) == {
        "managed": ["ezyhub-a"],
        "mcp_servers": ["ezyhub-kb"],
        "managed_files": {"ezyhub-a": ["SKILL.md", "scripts/run.py"]},
    }


def test_apply_mcp_servers_upserts_and_preserves_foreign_sections(tmp_path):
    helper = load_helper()
    config = tmp_path / "config.toml"
    config.write_text(
        'model_provider = "ezyhub"\n\n[mcp_servers.personal-notes]\nurl = "http://localhost:9/mcp"\n'
    )
    managed = helper.apply_mcp_servers(
        tmp_path,
        [{"name": "ezyhub-kb", "url": "https://kb.ezyapis.com/mcp",
          "bearer_token_env_var": "EZYHUB_KB_MCP_TOKEN", "enabled": True}],
        previous=set(),
    )
    text = config.read_text()
    assert managed == ["ezyhub-kb"]
    assert '[mcp_servers.ezyhub-kb]' in text
    assert 'url = "https://kb.ezyapis.com/mcp"' in text
    assert 'bearer_token_env_var = "EZYHUB_KB_MCP_TOKEN"' in text
    assert '[mcp_servers.personal-notes]' in text
    assert 'model_provider = "ezyhub"' in text

    # update in place (url changes), then removal of managed-but-gone
    helper.apply_mcp_servers(
        tmp_path,
        [{"name": "ezyhub-kb", "url": "https://new.example/mcp", "bearer_token_env_var": None, "enabled": True}],
        previous={"ezyhub-kb"},
    )
    text = config.read_text()
    assert 'url = "https://new.example/mcp"' in text
    assert text.count('[mcp_servers.ezyhub-kb]') == 1
    assert 'bearer_token_env_var' not in text.split('[mcp_servers.ezyhub-kb]')[1].split('[')[0]

    managed = helper.apply_mcp_servers(tmp_path, [], previous={"ezyhub-kb"})
    text = config.read_text()
    assert managed == []
    assert '[mcp_servers.ezyhub-kb]' not in text
    assert '[mcp_servers.personal-notes]' in text  # never touched


def test_apply_mcp_servers_refuses_foreign_name(tmp_path):
    helper = load_helper()
    import pytest
    with pytest.raises(RuntimeError):
        helper.apply_mcp_servers(tmp_path, [{"name": "rogue", "url": "https://x"}], previous=set())


def test_apply_mcp_servers_upsert_matches_header_with_trailing_comment(tmp_path):
    helper = load_helper()
    config = tmp_path / "config.toml"
    config.write_text(
        'model_provider = "ezyhub"\n\n'
        '[mcp_servers.ezyhub-kb]  # managed\n'
        'url = "https://old.example/mcp"\n'
    )
    helper.apply_mcp_servers(
        tmp_path,
        [{"name": "ezyhub-kb", "url": "https://new.example/mcp", "enabled": True}],
        previous={"ezyhub-kb"},
    )
    text = config.read_text()
    assert text.count("[mcp_servers.ezyhub-kb]") == 1
    assert 'url = "https://new.example/mcp"' in text
    assert "https://old.example/mcp" not in text


def test_apply_mcp_servers_upsert_matches_header_with_trailing_spaces(tmp_path):
    helper = load_helper()
    config = tmp_path / "config.toml"
    config.write_text(
        'model_provider = "ezyhub"\n\n'
        '[mcp_servers.ezyhub-kb]   \n'
        'url = "https://old.example/mcp"\n'
    )
    helper.apply_mcp_servers(
        tmp_path,
        [{"name": "ezyhub-kb", "url": "https://new.example/mcp", "enabled": True}],
        previous={"ezyhub-kb"},
    )
    text = config.read_text()
    assert text.count("[mcp_servers.ezyhub-kb]") == 1
    assert 'url = "https://new.example/mcp"' in text
    assert "https://old.example/mcp" not in text


def test_apply_mcp_servers_prune_removes_adjacent_subtable_and_keeps_foreign_sections(tmp_path):
    helper = load_helper()
    config = tmp_path / "config.toml"
    config.write_text(
        'model_provider = "ezyhub"\n\n'
        '[mcp_servers.ezyhub-kb]\n'
        'url = "https://kb/mcp"\n\n'
        '[mcp_servers.ezyhub-kb.headers]\n'
        'X-Foo = "bar"\n\n'
        '[mcp_servers.personal-notes]\n'
        'url = "http://localhost:9/mcp"\n'
    )
    managed = helper.apply_mcp_servers(tmp_path, [], previous={"ezyhub-kb"})
    text = config.read_text()
    assert managed == []
    assert '[mcp_servers.ezyhub-kb]' not in text
    assert '[mcp_servers.ezyhub-kb.headers]' not in text
    assert '[mcp_servers.personal-notes]' in text
    assert 'url = "http://localhost:9/mcp"' in text


def test_apply_mcp_servers_prune_removes_subtable_separated_by_foreign_section(tmp_path):
    helper = load_helper()
    config = tmp_path / "config.toml"
    config.write_text(
        '[mcp_servers.ezyhub-kb]\n'
        'url = "https://kb/mcp"\n\n'
        '[mcp_servers.personal-notes]\n'
        'url = "http://localhost:9/mcp"\n\n'
        '[mcp_servers.ezyhub-kb.headers]\n'
        'X-Foo = "bar"\n'
    )
    helper.apply_mcp_servers(tmp_path, [], previous={"ezyhub-kb"})
    text = config.read_text()
    assert '[mcp_servers.ezyhub-kb]' not in text
    assert '[mcp_servers.ezyhub-kb.headers]' not in text
    assert '[mcp_servers.personal-notes]' in text
    assert 'url = "http://localhost:9/mcp"' in text


def test_apply_mcp_servers_update_drops_stale_adjacent_subtable(tmp_path):
    helper = load_helper()
    config = tmp_path / "config.toml"
    config.write_text(
        '[mcp_servers.ezyhub-kb]\n'
        'url = "https://old/mcp"\n\n'
        '[mcp_servers.ezyhub-kb.headers]\n'
        'X-Foo = "bar"\n'
    )
    helper.apply_mcp_servers(
        tmp_path,
        [{"name": "ezyhub-kb", "url": "https://new/mcp", "enabled": True}],
        previous={"ezyhub-kb"},
    )
    text = config.read_text()
    assert text.count('[mcp_servers.ezyhub-kb]') == 1
    assert '[mcp_servers.ezyhub-kb.headers]' not in text
    assert 'url = "https://new/mcp"' in text


def test_apply_mcp_servers_leaves_foreign_prefixed_subtable_untouched(tmp_path):
    helper = load_helper()
    config = tmp_path / "config.toml"
    config.write_text(
        '[mcp_servers.ezyhub-kb]\n'
        'url = "https://kb/mcp"\n\n'
        '[mcp_servers.ezyhub-kb2.headers]\n'
        'X-Foo = "bar"\n'
    )
    helper.apply_mcp_servers(tmp_path, [], previous={"ezyhub-kb"})
    text = config.read_text()
    assert '[mcp_servers.ezyhub-kb]' not in text
    assert '[mcp_servers.ezyhub-kb2.headers]' in text
    assert 'X-Foo = "bar"' in text


def test_replace_first_and_blank_rest_does_not_interpret_backreferences():
    helper = load_helper()
    import re
    pattern = re.compile(r"(?m)^X.*\n?")
    text = "X one\nkeep\nX two\n"
    result = helper._replace_first_and_blank_rest(pattern, "value-\\g<0>-marker\n", text)
    assert result == "value-\\g<0>-marker\nkeep\n"


def test_apply_mcp_servers_rejects_hijack_name(tmp_path):
    helper = load_helper()
    import pytest
    with pytest.raises(RuntimeError):
        helper.apply_mcp_servers(
            tmp_path,
            [{"name": "ezyhub-x]\n[hijack", "url": "https://x"}],
            previous=set(),
        )


def test_apply_mcp_servers_rejects_url_with_quote(tmp_path):
    helper = load_helper()
    import pytest
    with pytest.raises(RuntimeError):
        helper.apply_mcp_servers(
            tmp_path,
            [{"name": "ezyhub-kb", "url": 'https://evil/"; injected = true'}],
            previous=set(),
        )


def test_apply_mcp_servers_rejects_url_with_backslash(tmp_path):
    helper = load_helper()
    import pytest
    with pytest.raises(RuntimeError):
        helper.apply_mcp_servers(
            tmp_path,
            [{"name": "ezyhub-kb", "url": "https://evil/\\g<0>"}],
            previous=set(),
        )


def test_sync_skills_applies_skills_and_mcp(tmp_path, monkeypatch):
    helper = load_helper()
    codex_home = tmp_path / "codex-home"
    skills_dir = codex_home / "skills"
    skills_dir.mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr(helper, "read_codex_key", lambda: "sk-test")
    monkeypatch.setattr(
        helper,
        "request_json",
        lambda method, path, **kwargs: {
            "role": "default",
            "skills": [{"name": "ezyhub-default", "files": skill_files("ezyhub-default", "Body.")}],
            "mcp_servers": [{"name": "ezyhub-kb", "url": "https://kb/mcp",
                             "bearer_token_env_var": "EZYHUB_KB_MCP_TOKEN", "enabled": True}],
            "namespace": "ezyhub",
        },
    )
    helper.cmd_sync_skills(argparse.Namespace(backend_url="http://x"))
    assert (skills_dir / "ezyhub-default" / "SKILL.md").exists()
    assert '[mcp_servers.ezyhub-kb]' in (codex_home / "config.toml").read_text()
    manifest = helper.load_manifest(skills_dir)
    assert manifest["mcp_servers"] == ["ezyhub-kb"]


def test_sync_skills_without_mcp_servers_key_leaves_config_and_manifest_untouched(tmp_path, monkeypatch):
    helper = load_helper()
    codex_home = tmp_path / "codex-home"
    skills_dir = codex_home / "skills"
    skills_dir.mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    helper.write_manifest(skills_dir, ["ezyhub-default"], ["ezyhub-kb"])
    config_text = (
        'model_provider = "ezyhub"\n\n'
        '[mcp_servers.ezyhub-kb]\n'
        'url = "https://kb.ezyapis.com/mcp"\n'
        'bearer_token_env_var = "EZYHUB_KB_MCP_TOKEN"\n\n'
        '[mcp_servers.personal-notes]\n'
        'url = "http://localhost:9/mcp"\n'
    )
    (codex_home / "config.toml").write_text(config_text, encoding="utf-8")

    monkeypatch.setattr(helper, "read_codex_key", lambda: "sk-test")
    monkeypatch.setattr(
        helper,
        "request_json",
        lambda method, path, **kwargs: {
            "role": "default",
            "skills": [{"name": "ezyhub-default", "files": skill_files("ezyhub-default", "Body.")}],
        },
    )

    helper.cmd_sync_skills(argparse.Namespace(backend_url="http://x"))

    assert (codex_home / "config.toml").read_bytes() == config_text.encode("utf-8")
    manifest = helper.load_manifest(skills_dir)
    assert manifest["mcp_servers"] == ["ezyhub-kb"]


def test_enroll_backend_dev_complete_lets_backend_issue_key(monkeypatch):
    helper = load_helper()
    monkeypatch.setenv("EZYHUB_CODEX_KEY", "sk-existing-local-key")

    calls: list[tuple[str, str, dict | None, dict | None]] = []

    def fake_request_json(method, path, **kwargs):
        calls.append((method, path, kwargs.get("body"), kwargs.get("query")))
        if method == "POST" and path == "/enroll/sessions":
            return {
                "session_id": "session-1",
                "device_secret": "device-secret",
                "browser_url": "http://backend/dev/enroll",
            }
        if method == "POST" and path == "/dev/enroll/complete":
            return {"ok": True}
        if method == "GET" and path == "/enroll/sessions/session-1/result":
            return {"status": "complete", "key": "sk-issued-by-backend", "employee_id": 1}
        raise AssertionError(f"unexpected request: {method} {path}")

    configured: list[tuple[str, str, str]] = []
    monkeypatch.setattr(helper, "request_json", fake_request_json)
    monkeypatch.setattr(helper, "configure_codex_with_key", lambda key, base_url, model: configured.append((key, base_url, model)))
    monkeypatch.setattr(helper, "cmd_sync_skills", lambda args: (_ for _ in ()).throw(AssertionError("sync should be skipped")))

    args = argparse.Namespace(
        backend_url="http://backend",
        no_open_browser=True,
        dev_complete=True,
        dev_google_sub="sub-dev",
        dev_google_email="dev@example.com",
        dev_name="Dev User",
        dev_role="engineering",
        poll_timeout_seconds=1,
        poll_interval_seconds=0,
        base_url="https://api.ezyapis.com/v1",
        model="gpt-5.6-sol",
        skip_sync_skills=True,
        skip_auto_sync=True,
        auto_sync_interval_hours=4,
    )

    helper.cmd_enroll_backend(args)

    dev_complete_call = next(call for call in calls if call[1] == "/dev/enroll/complete")
    assert dev_complete_call[2] == {
        "session_id": "session-1",
        "google_sub": "sub-dev",
        "google_email": "dev@example.com",
        "name": "Dev User",
        "role": "engineering",
    }
    assert configured == [("sk-issued-by-backend", "https://api.ezyapis.com/v1", "gpt-5.6-sol")]


def test_enroll_backend_one_shot_chain(monkeypatch, capsys):
    helper = load_helper()
    calls = []
    monkeypatch.setattr(helper, "request_json", lambda method, path, **k: {
        "POST /enroll/sessions": {"session_id": "s1", "device_secret": "d1", "browser_url": "https://hub/codex/enroll?session_id=s1"},
        "GET /enroll/sessions/s1/result": {"status": "complete", "key": "sk-k", "employee_id": 1, "role": "default"},
    }[f"{method} {path}"])
    monkeypatch.setattr(helper, "configure_codex_with_key", lambda *a: calls.append("configure"))
    monkeypatch.setattr(helper, "cmd_sync_skills", lambda a: calls.append("sync"))

    def fake_install_auto_sync(hours, url):
        calls.append(f"auto-sync-{hours}")
        return "AUTO-SYNC-SUMMARY-XYZ"

    monkeypatch.setattr(helper, "install_auto_sync", fake_install_auto_sync)
    args = argparse.Namespace(
        backend_url="https://b", no_open_browser=True, dev_complete=False,
        poll_timeout_seconds=1, poll_interval_seconds=0.01,
        base_url="https://gw/v1", model="gpt-5.6-sol",
        skip_sync_skills=False, skip_auto_sync=False, auto_sync_interval_hours=4,
    )
    helper.cmd_enroll_backend(args)
    assert calls == ["configure", "sync", "auto-sync-4"]
    out = capsys.readouterr().out
    assert "AUTO-SYNC-SUMMARY-XYZ" in out
    assert "Open a new Codex App thread" in out


def test_enroll_backend_prints_resume_command_on_sync_failure(monkeypatch, capsys):
    helper = load_helper()
    monkeypatch.setattr(helper, "request_json", lambda method, path, **k: {
        "POST /enroll/sessions": {"session_id": "s1", "device_secret": "d1", "browser_url": "https://hub/x"},
        "GET /enroll/sessions/s1/result": {"status": "complete", "key": "sk-k", "employee_id": 1, "role": "default"},
    }[f"{method} {path}"])
    monkeypatch.setattr(helper, "configure_codex_with_key", lambda *a: None)
    def boom(a):
        raise RuntimeError("sync exploded")
    monkeypatch.setattr(helper, "cmd_sync_skills", boom)
    import pytest
    args = argparse.Namespace(
        backend_url="https://b", no_open_browser=True, dev_complete=False,
        poll_timeout_seconds=1, poll_interval_seconds=0.01,
        base_url="https://gw/v1", model="gpt-5.6-sol",
        skip_sync_skills=False, skip_auto_sync=False, auto_sync_interval_hours=4,
    )
    with pytest.raises(RuntimeError):
        helper.cmd_enroll_backend(args)
    out = capsys.readouterr().out
    assert "Resume with:" in out and "sync-skills" in out and "install-auto-sync" in out


def test_employee_defaults_use_public_domains():
    helper = load_helper()
    configure = load_configure_helper()
    plugin_root = Path(__file__).resolve().parents[1]
    mcp_config = json.loads((plugin_root / ".mcp.json").read_text(encoding="utf-8"))

    defaults = [
        helper.DEFAULT_BACKEND_URL,
        helper.DEFAULT_KB_HEALTH_URL,
        helper.DEFAULT_GATEWAY_BASE_URL,
        configure.DEFAULT_BASE_URL,
        mcp_config["mcpServers"]["ezyhub-kb"]["url"],
    ]

    assert all(value.startswith("https://") for value in defaults)
    assert all(not value.startswith("http://") for value in defaults)
    assert all(not any(char.isdigit() for char in value.split("://", 1)[1].split("/", 1)[0]) for value in defaults)
    assert helper.DEFAULT_GATEWAY_BASE_URL == "https://api.ezyapis.com/v1"

def test_configure_codex_app_kb_token_uses_launchctl_without_printing_token(monkeypatch, capsys):
    helper = load_helper()
    calls = []
    monkeypatch.setenv(helper.CODEX_APP_KB_TOKEN_ENV, "app-token")
    monkeypatch.setattr(helper, "launchctl_setenv", lambda name, value: calls.append((name, value)))

    helper.cmd_configure_codex_app_kb_token(argparse.Namespace(token_file=None, prompt_token=False))

    assert calls == [(helper.CODEX_APP_KB_TOKEN_ENV, "app-token")]
    output = capsys.readouterr().out
    assert "app-token" not in output
    assert helper.CODEX_APP_KB_TOKEN_ENV in output


def test_configure_codex_app_client_key_uses_env_without_printing_key(monkeypatch, capsys):
    helper = load_helper()
    calls = []
    monkeypatch.setenv(helper.CODEX_CLIENT_KEY_ENV, "sk-client-secret")
    monkeypatch.setattr(
        helper,
        "configure_codex_with_key",
        lambda key, base_url, model: calls.append((key, base_url, model)),
    )

    helper.cmd_configure_codex_app_client_key(
        argparse.Namespace(
            key_file=None,
            prompt_key=False,
            base_url="http://gateway/v1",
            model="gpt-5.6-sol",
        )
    )

    assert calls == [("sk-client-secret", "http://gateway/v1", "gpt-5.6-sol")]
    assert "sk-client-secret" not in capsys.readouterr().out


def test_configure_codex_writes_env_key_and_removes_ezyhub_auth_key(tmp_path):
    configure = load_configure_helper()
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text(
        json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": "sk-ezyhub-old"}),
        encoding="utf-8",
    )
    (codex_home / "config.toml").write_text(
        "\n".join(
            [
                'model_provider = "ezyapis"',
                'model = "gpt-5.6-sol"',
                "",
                "[model_providers.ezyapis]",
                'name = "ezyapis"',
                'base_url = "https://api.ezyapis.com/v1"',
                'wire_api = "responses"',
                'experimental_bearer_token = "sk-ezyhub-inline-old"',
            ]
        ),
        encoding="utf-8",
    )

    env_path = configure.write_codex_env_key(codex_home, "sk-ezyhub-new")
    config_path = configure.merge_config(codex_home, "https://api.ezyapis.com/v1", "gpt-5.6-sol", "sk-ezyhub-new")
    removed = configure.remove_previous_ezyhub_auth_key(codex_home, "sk-ezyhub-new")

    assert env_path.read_text(encoding="utf-8") == "EZYHUB_CODEX_KEY=sk-ezyhub-new\n"
    config_text = config_path.read_text(encoding="utf-8")
    # provider "ezyapis" is retained (base_url matches the company gateway)
    assert 'model_provider = "ezyapis"' in config_text
    assert "[model_providers.ezyapis]" in config_text
    assert "[model_providers.company]" not in config_text
    assert "[model_providers.ezyhub]" not in config_text
    assert 'experimental_bearer_token = "sk-ezyhub-new"' in config_text
    assert 'requires_openai_auth = true' in config_text
    assert "env_key" not in config_text
    assert "sk-ezyhub-inline-old" not in config_text
    assert removed is True
    auth_payload = json.loads((codex_home / "auth.json").read_text(encoding="utf-8"))
    assert "OPENAI_API_KEY" not in auth_payload


def test_codex_app_config_status_reports_ready_without_printing_key(tmp_path, monkeypatch):
    helper = load_helper()
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        '\n'.join(
            [
                'model_provider = "ezyhub"',
                'model = "gpt-5.6-sol"',
                "",
                "[model_providers.ezyhub]",
                'name = "EzyHub"',
                'base_url = "http://gateway/v1"',
                'wire_api = "responses"',
            ]
        ),
        encoding="utf-8",
    )
    (codex_home / "auth.json").write_text(json.dumps({"OPENAI_API_KEY": "sk-client-secret"}), encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    payload = helper.codex_app_config_status(
        expected_base_url="http://gateway/v1",
        expected_model="gpt-5.6-sol",
    )

    assert payload["ready"] is True
    assert payload["missing"] == []
    assert "sk-client-secret" not in json.dumps(payload)


def test_status_ready_for_retained_ezyapis_inline_token(tmp_path, monkeypatch):
    helper = load_helper()
    home = tmp_path / "codex"
    home.mkdir()
    (home / "config.toml").write_text(
        'model_provider = "ezyapis"\n'
        'model = "gpt-5.6-sol"\n'
        '[features]\n'
        'image_generation = false\n'
        '[model_providers.ezyapis]\n'
        'name = "EzyHub"\n'
        'base_url = "https://api.ezyapis.com/v1"\n'
        'wire_api = "responses"\n'
        'experimental_bearer_token = "sk-ezyhub-secret"\n'
        'requires_openai_auth = true\n',
        encoding="utf-8",
    )
    (home / "auth.json").write_text(
        '{"tokens": {"access_token": "x"}, "account": {"type": "chatgpt"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(helper, "codex_home", lambda: home)

    status = helper.codex_app_config_status(
        expected_base_url="https://api.ezyapis.com/v1",
        expected_model="gpt-5.6-sol",
    )

    assert status["ready"] is True
    assert status["missing"] == []
    assert status["observed"]["provider_id_retained"] == "ezyapis"
    assert status["observed"]["requires_openai_auth"] is True
    assert status["observed"]["inline_token_present"] is True
    assert status["observed"]["chatgpt_login"] is True
    assert status["observed"]["image_generation_off"] is True
    assert "sk-ezyhub-secret" not in json.dumps(status)


def test_codex_app_config_status_reports_legacy_ezyapis_inline_token_ready(tmp_path, monkeypatch):
    helper = load_helper()
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        "\n".join(
            [
                'model_provider = "ezyapis"',
                'model = "gpt-5.6-sol"',
                "",
                "[model_providers.ezyapis]",
                'name = "ezyapis"',
                'base_url = "https://api.ezyapis.com/v1"',
                'wire_api = "responses"',
                'experimental_bearer_token = "sk-cliproxy-secret"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    payload = helper.codex_app_config_status(
        expected_base_url="http://gateway/v1",
        expected_model="gpt-5.6-sol",
    )

    # "ezyapis" is an EzyHub-managed provider id pointed at the company gateway,
    # so a retained inline-token config now counts as ready (mixed-auth).
    assert payload["ready"] is True
    assert payload["missing"] == []
    assert payload["configured"]["provider_experimental_bearer_token"] is True
    assert payload["observed"]["provider_id_retained"] == "ezyapis"
    assert "sk-cliproxy-secret" not in json.dumps(payload)


def test_codex_app_config_status_accepts_provider_env_key(tmp_path, monkeypatch):
    helper = load_helper()
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        "\n".join(
            [
                'model_provider = "ezyhub"',
                'model = "gpt-5.6-sol"',
                "",
                "[model_providers.ezyhub]",
                'name = "EzyHub"',
                'base_url = "http://gateway/v1"',
                'wire_api = "responses"',
                'env_key = "EZYHUB_CODEX_KEY"',
            ]
        ),
        encoding="utf-8",
    )
    (codex_home / ".env").write_text("EZYHUB_CODEX_KEY=sk-client-secret\n", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.delenv("EZYHUB_CODEX_KEY", raising=False)

    payload = helper.codex_app_config_status(
        expected_base_url="http://gateway/v1",
        expected_model="gpt-5.6-sol",
    )

    assert payload["ready"] is True
    assert payload["missing"] == []
    assert payload["configured"]["provider_env_key"] is True
    assert payload["configured"]["provider_env_key_value"] is True
    assert "sk-client-secret" not in json.dumps(payload)


def test_read_codex_key_prefers_codex_env_file(tmp_path, monkeypatch):
    helper = load_helper()
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        "\n".join(
            [
                'model_provider = "ezyhub"',
                "",
                "[model_providers.ezyhub]",
                'env_key = "EZYHUB_CODEX_KEY"',
            ]
        ),
        encoding="utf-8",
    )
    (codex_home / ".env").write_text("EZYHUB_CODEX_KEY=sk-env-file-key\n", encoding="utf-8")
    (codex_home / "auth.json").write_text(json.dumps({"OPENAI_API_KEY": "sk-auth-key"}), encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.delenv("EZYHUB_CODEX_KEY", raising=False)

    assert helper.read_codex_key() == "sk-env-file-key"


def test_read_codex_key_prefers_env_file_over_inline(tmp_path, monkeypatch):
    helper = load_helper()
    home = tmp_path / "codex"
    home.mkdir()
    (home / ".env").write_text('EZYHUB_CODEX_KEY=sk-ezyhub-fromenv\n')
    (home / "config.toml").write_text(
        'model_provider = "ezyapis"\n[model_providers.ezyapis]\n'
        'experimental_bearer_token = "sk-ezyhub-inline"\n'
    )
    monkeypatch.setattr(helper, "codex_home", lambda: home)
    monkeypatch.delenv("EZYHUB_CODEX_KEY", raising=False)
    assert helper.read_codex_key() == "sk-ezyhub-fromenv"


def test_read_codex_key_falls_back_to_inline_token(tmp_path, monkeypatch):
    helper = load_helper()
    home = tmp_path / "codex"
    home.mkdir()
    (home / "config.toml").write_text(
        'model_provider = "company"\n[model_providers.company]\n'
        'experimental_bearer_token = "sk-ezyhub-inline2"\n'
    )
    monkeypatch.setattr(helper, "codex_home", lambda: home)
    monkeypatch.delenv("EZYHUB_CODEX_KEY", raising=False)
    assert helper.read_codex_key() == "sk-ezyhub-inline2"


def test_read_codex_key_never_forwards_foreign_inline_token(tmp_path, monkeypatch):
    helper = load_helper()
    home = tmp_path / "codex"
    home.mkdir()
    # Active provider is NOT EzyHub-owned (foreign id, foreign name, foreign base_url):
    # its inline bearer token is a third-party secret and must never be sent to the backend.
    (home / "config.toml").write_text(
        'model_provider = "openai"\n'
        "[model_providers.openai]\n"
        'name = "OpenAI"\n'
        'base_url = "https://api.openai.com/v1"\n'
        'experimental_bearer_token = "sk-foreign-inline"\n'
    )
    (home / "auth.json").write_text(json.dumps({"OPENAI_API_KEY": "sk-ezyhub-legacy"}), encoding="utf-8")
    monkeypatch.setattr(helper, "codex_home", lambda: home)
    monkeypatch.delenv("EZYHUB_CODEX_KEY", raising=False)
    assert helper.read_codex_key() == "sk-ezyhub-legacy"


def test_codex_app_config_status_reports_missing_without_reading_key(tmp_path, monkeypatch):
    helper = load_helper()
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text('model_provider = "openai"\n', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    payload = helper.codex_app_config_status(
        expected_base_url="http://gateway/v1",
        expected_model="gpt-5.6-sol",
    )

    assert payload["ready"] is False
    assert ".env:EZYHUB_CODEX_KEY or auth.json" in payload["missing"]
    assert "provider.env_key/.env, provider.experimental_bearer_token, or auth.OPENAI_API_KEY" in payload["missing"]
    assert "model_provider=ezyhub" in payload["missing"]


def test_read_codex_client_key_rejects_management_hash(monkeypatch):
    helper = load_helper()
    monkeypatch.setenv(helper.CODEX_CLIENT_KEY_ENV, "$2a$10$abcdef")

    try:
        helper.read_codex_client_key(argparse.Namespace(key_file=None, prompt_key=False))
    except RuntimeError as exc:
        assert "bcrypt management hash" in str(exc)
        assert "employee/client key" in str(exc)
    else:
        raise AssertionError("read_codex_client_key should reject management hashes")


def test_read_codex_client_key_rejects_non_client_key(monkeypatch):
    helper = load_helper()
    monkeypatch.setenv(helper.CODEX_CLIENT_KEY_ENV, "management-secret")

    try:
        helper.read_codex_client_key(argparse.Namespace(key_file=None, prompt_key=False))
    except RuntimeError as exc:
        assert "does not look like a CLIProxyAPI employee/client key" in str(exc)
    else:
        raise AssertionError("read_codex_client_key should reject non-client keys")


def test_clear_codex_app_kb_token_uses_launchctl(monkeypatch, capsys):
    helper = load_helper()
    calls = []
    monkeypatch.setattr(helper, "launchctl_unsetenv", lambda name: calls.append(name))

    helper.cmd_clear_codex_app_kb_token(argparse.Namespace())

    assert calls == [helper.CODEX_APP_KB_TOKEN_ENV]
    assert helper.CODEX_APP_KB_TOKEN_ENV in capsys.readouterr().out


def test_codex_app_kb_token_status_masks_value(monkeypatch, capsys):
    helper = load_helper()
    monkeypatch.setattr(helper, "launchctl_getenv", lambda name: "app-token")

    helper.cmd_codex_app_kb_token_status(argparse.Namespace())

    output = capsys.readouterr().out
    assert "app-token" not in output
    assert '"configured": true' in output


def test_doctor_text_mode_uses_built_report(monkeypatch, capsys):
    helper = load_helper()
    monkeypatch.setattr(
        helper,
        "build_doctor_report",
        lambda args: {
            "ready": False,
            "checks": {
                "key_backend": {"ok": True},
                "kb_mcp": {"ok": False, "payload": {"configured": {"workspace_id": False}}},
            },
        },
    )
    monkeypatch.setattr(
        helper,
        "auto_sync_status",
        lambda: {"installed": False, "platform": "linux", "detail": "no crontab entry"},
    )

    helper.cmd_doctor(argparse.Namespace(backend_url="http://backend", json=False, strict=False))

    output = capsys.readouterr().out
    assert "key_backend: ok" in output
    assert "kb_mcp: not ready" in output


def test_doctor_reports_auto_sync_status(monkeypatch, capsys):
    helper = load_helper()
    monkeypatch.setattr(
        helper,
        "build_doctor_report",
        lambda args: {"ready": True, "checks": {"key_backend": {"ok": True}}},
    )
    monkeypatch.setattr(
        helper,
        "auto_sync_status",
        lambda: {"installed": True, "platform": "linux", "detail": "cron line present"},
    )

    helper.cmd_doctor(argparse.Namespace(backend_url="http://backend", json=False, strict=False))

    output = capsys.readouterr().out.lower()
    assert "auto-sync" in output
    assert "installed" in output


def test_doctor_json_mode_includes_auto_sync_status(monkeypatch, capsys):
    helper = load_helper()
    monkeypatch.setattr(
        helper,
        "build_doctor_report",
        lambda args: {"ready": True, "checks": {}},
    )
    monkeypatch.setattr(
        helper,
        "auto_sync_status",
        lambda: {"installed": False, "platform": "linux", "detail": "no crontab entry"},
    )

    helper.cmd_doctor(argparse.Namespace(backend_url="http://backend", json=True, strict=False))

    payload = json.loads(capsys.readouterr().out)
    assert payload["auto_sync"] == {
        "installed": False,
        "platform": "linux",
        "detail": "no crontab entry",
    }


def _fake_run_recording(calls, *, returncode=0, stdout="", stderr=""):
    def fake_run(cmd, **kwargs):
        calls.append(cmd)

        class R:
            pass

        result = R()
        result.returncode = returncode
        result.stdout = stdout
        result.stderr = stderr
        return result

    return fake_run


def test_auto_sync_builders(tmp_path):
    helper = load_helper()
    line = helper.build_cron_line(4, "/p/ezyhub_backend.py", "https://b")
    assert line.startswith("0 */4 * * * ")
    assert "sync-skills" in line and line.endswith("# ezyhub-codex-auto-sync")
    assert "https://b" in line

    merged = helper.merge_crontab_text("keep me\nold sync # ezyhub-codex-auto-sync\n", line)
    assert "keep me" in merged
    assert merged.count("ezyhub-codex-auto-sync") == 1
    assert helper.merge_crontab_text(merged, None).count("ezyhub-codex-auto-sync") == 0

    plist = helper.build_launchd_plist(4, "/p/ezyhub_backend.py", "https://b")
    assert "com.ezyhub.codex-auto-sync" in plist and "<integer>14400</integer>" in plist

    args = helper.build_schtasks_create_args(4, "C:/p/ezyhub_backend.py", "https://b")
    assert args[:2] == ["schtasks", "/Create"] and "/F" in args and "EzyHubCodexAutoSync" in args


def test_build_cron_line_quotes_paths_and_urls_with_spaces():
    helper = load_helper()
    import shlex
    helper_path = "/p/ezy hub/ezyhub_backend.py"
    backend_url = "https://b/x y"
    line = helper.build_cron_line(4, helper_path, backend_url)
    assert shlex.quote(helper_path) in line
    assert shlex.quote(backend_url) in line
    # the trailing marker must stay outside any quoting
    assert line.endswith("# ezyhub-codex-auto-sync")
    assert "'# ezyhub-codex-auto-sync'" not in line


def test_build_schtasks_create_args_quotes_backend_url(monkeypatch):
    helper = load_helper()
    args = helper.build_schtasks_create_args(4, "C:/p/ezyhub_backend.py", "https://b/x y")
    command = args[args.index("/TR") + 1]
    assert '"https://b/x y"' in command
    assert '"C:/p/ezyhub_backend.py"' in command


def test_install_auto_sync_rejects_interval_out_of_bounds(monkeypatch):
    helper = load_helper()
    import pytest
    calls: list = []
    monkeypatch.setattr(helper, "current_platform", lambda: "linux")
    monkeypatch.setattr(helper.subprocess, "run", _fake_run_recording(calls))
    with pytest.raises(RuntimeError):
        helper.install_auto_sync(0, "https://b")
    with pytest.raises(RuntimeError):
        helper.install_auto_sync(24, "https://b")
    assert calls == []


def test_install_auto_sync_accepts_boundary_intervals(monkeypatch):
    helper = load_helper()
    calls: list = []
    monkeypatch.setattr(helper, "current_platform", lambda: "linux")
    monkeypatch.setattr(helper.subprocess, "run", _fake_run_recording(calls))
    helper.install_auto_sync(1, "https://b")
    helper.install_auto_sync(23, "https://b")


def test_install_auto_sync_linux_idempotent(monkeypatch):
    helper = load_helper()
    state = {"crontab": "existing job\n"}

    def fake_run(cmd, **kwargs):
        class R:
            returncode = 0
            stdout = state["crontab"]
            stderr = ""

        if cmd[:2] == ["crontab", "-l"]:
            return R()
        if cmd == ["crontab", "-"]:
            state["crontab"] = kwargs["input"]
            return R()
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(helper, "current_platform", lambda: "linux")
    monkeypatch.setattr(helper.subprocess, "run", fake_run)
    helper.install_auto_sync(4, "https://b")
    helper.install_auto_sync(4, "https://b")
    assert state["crontab"].count("ezyhub-codex-auto-sync") == 1
    assert "existing job" in state["crontab"]
    helper.uninstall_auto_sync()
    assert "ezyhub-codex-auto-sync" not in state["crontab"]
    assert "existing job" in state["crontab"]


def test_install_auto_sync_linux_treats_missing_crontab_as_empty(monkeypatch):
    helper = load_helper()
    state = {"crontab": None}

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["crontab", "-l"]:
            class R:
                returncode = 1
                stdout = ""
                stderr = "no crontab for user"

            return R()
        if cmd == ["crontab", "-"]:
            state["crontab"] = kwargs["input"]

            class R:
                returncode = 0
                stdout = ""
                stderr = ""

            return R()
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(helper, "current_platform", lambda: "linux")
    monkeypatch.setattr(helper.subprocess, "run", fake_run)
    helper.install_auto_sync(4, "https://b")
    assert state["crontab"].count("ezyhub-codex-auto-sync") == 1


def test_install_auto_sync_darwin_writes_plist_and_loads(tmp_path, monkeypatch):
    helper = load_helper()
    monkeypatch.setenv("HOME", str(tmp_path))
    calls: list = []
    monkeypatch.setattr(helper, "current_platform", lambda: "darwin")
    monkeypatch.setattr(helper.subprocess, "run", _fake_run_recording(calls))

    helper.install_auto_sync(4, "https://b")

    plist_path = tmp_path / "Library" / "LaunchAgents" / "com.ezyhub.codex-auto-sync.plist"
    assert plist_path.exists()
    assert "com.ezyhub.codex-auto-sync" in plist_path.read_text(encoding="utf-8")
    assert calls == [
        ["launchctl", "unload", str(plist_path)],
        ["launchctl", "load", str(plist_path)],
    ]


def test_uninstall_auto_sync_darwin_unloads_and_deletes_plist(tmp_path, monkeypatch):
    helper = load_helper()
    monkeypatch.setenv("HOME", str(tmp_path))
    plist_dir = tmp_path / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True)
    plist_path = plist_dir / "com.ezyhub.codex-auto-sync.plist"
    plist_path.write_text("existing", encoding="utf-8")
    calls: list = []
    monkeypatch.setattr(helper, "current_platform", lambda: "darwin")
    monkeypatch.setattr(helper.subprocess, "run", _fake_run_recording(calls))

    helper.uninstall_auto_sync()

    assert calls == [["launchctl", "unload", str(plist_path)]]
    assert not plist_path.exists()


def test_install_auto_sync_win32_creates_scheduled_task(monkeypatch):
    helper = load_helper()
    calls: list = []
    monkeypatch.setattr(helper, "current_platform", lambda: "win32")
    monkeypatch.setattr(helper.subprocess, "run", _fake_run_recording(calls))

    helper.install_auto_sync(4, "https://b")

    helper_path = str(Path(helper.__file__).resolve())
    expected = helper.build_schtasks_create_args(4, helper_path, "https://b")
    assert calls == [expected]


def test_uninstall_auto_sync_win32_deletes_scheduled_task(monkeypatch):
    helper = load_helper()
    calls: list = []
    monkeypatch.setattr(helper, "current_platform", lambda: "win32")
    monkeypatch.setattr(helper.subprocess, "run", _fake_run_recording(calls))

    helper.uninstall_auto_sync()

    assert calls == [["schtasks", "/Delete", "/TN", "EzyHubCodexAutoSync", "/F"]]


def test_uninstall_auto_sync_win32_not_installed_is_graceful(monkeypatch):
    helper = load_helper()
    calls: list = []
    monkeypatch.setattr(helper, "current_platform", lambda: "win32")
    monkeypatch.setattr(
        helper.subprocess,
        "run",
        _fake_run_recording(calls, returncode=1, stderr="ERROR: The system cannot find the file specified."),
    )

    first = helper.uninstall_auto_sync()
    second = helper.uninstall_auto_sync()

    assert first == "Auto-sync task was not installed."
    assert second == "Auto-sync task was not installed."
    assert calls == [["schtasks", "/Delete", "/TN", "EzyHubCodexAutoSync", "/F"]] * 2


def test_auto_sync_status_linux_installed(monkeypatch):
    helper = load_helper()
    calls: list = []
    monkeypatch.setattr(helper, "current_platform", lambda: "linux")
    monkeypatch.setattr(
        helper.subprocess,
        "run",
        _fake_run_recording(calls, stdout="0 */4 * * * foo # ezyhub-codex-auto-sync\n"),
    )

    status = helper.auto_sync_status()
    assert status == {"installed": True, "platform": "linux", "detail": status["detail"]}
    assert status["installed"] is True


def test_auto_sync_status_linux_not_installed(monkeypatch):
    helper = load_helper()
    calls: list = []
    monkeypatch.setattr(helper, "current_platform", lambda: "linux")
    monkeypatch.setattr(
        helper.subprocess,
        "run",
        _fake_run_recording(calls, returncode=1, stderr="no crontab for user"),
    )

    status = helper.auto_sync_status()
    assert status["installed"] is False
    assert status["platform"] == "linux"


def test_auto_sync_status_darwin(tmp_path, monkeypatch):
    helper = load_helper()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(helper, "current_platform", lambda: "darwin")

    assert helper.auto_sync_status()["installed"] is False

    plist_dir = tmp_path / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True)
    (plist_dir / "com.ezyhub.codex-auto-sync.plist").write_text("x", encoding="utf-8")

    assert helper.auto_sync_status()["installed"] is True


def test_auto_sync_status_win32(monkeypatch):
    helper = load_helper()
    calls: list = []
    monkeypatch.setattr(helper, "current_platform", lambda: "win32")
    monkeypatch.setattr(helper.subprocess, "run", _fake_run_recording(calls, returncode=0))

    status = helper.auto_sync_status()
    assert status["installed"] is True
    assert status["platform"] == "win32"


def test_auto_sync_status_never_raises_on_subprocess_error(monkeypatch):
    helper = load_helper()

    def fake_run(cmd, **kwargs):
        raise OSError("boom")

    monkeypatch.setattr(helper, "current_platform", lambda: "linux")
    monkeypatch.setattr(helper.subprocess, "run", fake_run)

    status = helper.auto_sync_status()
    assert status["installed"] is False
    assert "boom" in status["detail"]


def test_parse_args_install_auto_sync_default_interval(monkeypatch):
    helper = load_helper()
    monkeypatch.setattr(helper.sys, "argv", ["ezyhub_backend.py", "install-auto-sync"])
    args = helper.parse_args()
    assert args.command == "install-auto-sync"
    assert args.interval_hours == 4


def test_cmd_install_auto_sync_prints_summary_and_interval(monkeypatch, capsys):
    helper = load_helper()
    monkeypatch.setattr(
        helper,
        "install_auto_sync",
        lambda interval_hours, backend_url: f"Installed for {backend_url}",
    )

    helper.cmd_install_auto_sync(argparse.Namespace(backend_url="https://b", interval_hours=4))

    output = capsys.readouterr().out
    assert "Installed for https://b" in output
    assert "4" in output


def test_cmd_uninstall_auto_sync_prints_summary(monkeypatch, capsys):
    helper = load_helper()
    monkeypatch.setattr(helper, "uninstall_auto_sync", lambda: "Removed job")

    helper.cmd_uninstall_auto_sync(argparse.Namespace(backend_url="https://b"))

    output = capsys.readouterr().out
    assert "Removed job" in output




def test_publish_skill_posts_local_tree_and_prints_submission_id(tmp_path, monkeypatch, capsys):
    helper = load_helper()
    skill_dir = tmp_path / "ezyhub-mytool"
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "assets").mkdir()
    (skill_dir / "SKILL.md").write_text(skill_content("ezyhub-mytool", "body"), encoding="utf-8")
    (skill_dir / "scripts" / "run.py").write_text("print(1)\n", encoding="utf-8")
    (skill_dir / "assets" / "logo.bin").write_bytes(b"\x00\x01\x02")

    captured: dict = {}

    def fake_request_json(method, path, **kwargs):
        captured["method"] = method
        captured["path"] = path
        captured.update(kwargs)
        return {"submission_id": "sub-abc123"}

    monkeypatch.setattr(helper, "read_codex_key", lambda: "sk-secret-key")
    monkeypatch.setattr(helper, "request_json", fake_request_json)

    helper.cmd_publish_skill(
        argparse.Namespace(backend_url="http://backend", dir=str(skill_dir), role="engineering", name=None)
    )

    assert captured["method"] == "POST"
    assert captured["path"] == "/skills/submit"
    assert captured["token"] == "sk-secret-key"
    body = captured["body"]
    assert body["skill_name"] == "ezyhub-mytool"  # inferred from dir basename
    assert body["target_role"] == "engineering"
    assert sorted(f["path"] for f in body["files"]) == ["SKILL.md", "assets/logo.bin", "scripts/run.py"]
    logo = next(f for f in body["files"] if f["path"] == "assets/logo.bin")
    assert logo["encoding"] == "base64"
    assert base64.b64decode(logo["content"]) == b"\x00\x01\x02"

    output = capsys.readouterr().out
    assert "sub-abc123" in output
    assert "sk-secret-key" not in output


def test_publish_skill_rejects_non_company_name(tmp_path, monkeypatch, capsys):
    import pytest

    helper = load_helper()
    skill_dir = tmp_path / "mytool"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(skill_content("mytool", "body"), encoding="utf-8")

    monkeypatch.setattr(helper, "read_codex_key", lambda: "sk-secret-key")
    monkeypatch.setattr(
        helper,
        "request_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not POST an invalid name")),
    )

    with pytest.raises(RuntimeError, match="ezyhub-"):
        helper.cmd_publish_skill(
            argparse.Namespace(backend_url="http://backend", dir=str(skill_dir), role="engineering", name=None)
        )
    with pytest.raises(RuntimeError, match="ezyhub-"):
        helper.cmd_publish_skill(
            argparse.Namespace(backend_url="http://backend", dir=str(skill_dir), role="engineering", name="rogue")
        )

    # --name override with a company name is accepted
    monkeypatch.setattr(helper, "request_json", lambda method, path, **kwargs: {"submission_id": "sub-1"})
    helper.cmd_publish_skill(
        argparse.Namespace(backend_url="http://backend", dir=str(skill_dir), role="engineering", name="ezyhub-mytool")
    )
    output = capsys.readouterr().out
    assert "sub-1" in output
    assert "sk-secret-key" not in output


def test_publish_skill_local_reader_rejects_symlinks_and_oversize(tmp_path, monkeypatch):
    import pytest

    helper = load_helper()
    monkeypatch.setattr(helper, "read_codex_key", lambda: "sk-secret-key")
    monkeypatch.setattr(
        helper,
        "request_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not POST an unsafe bundle")),
    )

    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    linked = tmp_path / "ezyhub-linked"
    linked.mkdir()
    (linked / "SKILL.md").write_text(skill_content("ezyhub-linked", "body"), encoding="utf-8")
    (linked / "leak.md").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        helper.cmd_publish_skill(
            argparse.Namespace(backend_url="http://backend", dir=str(linked), role="engineering", name=None)
        )

    big = tmp_path / "ezyhub-big"
    big.mkdir()
    (big / "SKILL.md").write_text(skill_content("ezyhub-big", "body"), encoding="utf-8")
    (big / "big.txt").write_bytes(b"x" * (helper.FILE_MAX_BYTES + 1))
    with pytest.raises(ValueError, match="size cap"):
        helper.cmd_publish_skill(
            argparse.Namespace(backend_url="http://backend", dir=str(big), role="engineering", name=None)
        )

    with pytest.raises(RuntimeError, match="does not exist"):
        helper.cmd_publish_skill(
            argparse.Namespace(backend_url="http://backend", dir=str(tmp_path / "ezyhub-missing"), role="engineering", name=None)
        )


def test_select_retained_provider_keeps_ezyapis(tmp_path):
    configure = load_configure_helper()
    text = (
        'model_provider = "ezyapis"\n\n'
        '[model_providers.ezyapis]\n'
        'name = "EzyHub"\n'
        'base_url = "https://api.ezyapis.com/v1"\n'
        'wire_api = "responses"\n'
    )
    assert configure.select_retained_provider(text) == "ezyapis"


def test_select_retained_provider_keeps_company_by_baseurl(tmp_path):
    configure = load_configure_helper()
    text = (
        'model_provider = "company"\n\n'
        '[model_providers.company]\n'
        'base_url = "https://api.ezyapis.com/v1/"\n'
    )
    assert configure.select_retained_provider(text) == "company"


def test_select_retained_provider_new_install_defaults_ezyhub(tmp_path):
    configure = load_configure_helper()
    assert configure.select_retained_provider("") == "ezyhub"


def test_select_retained_provider_ignores_foreign_active_provider(tmp_path):
    configure = load_configure_helper()
    text = (
        'model_provider = "openai"\n\n'
        '[model_providers.openai]\n'
        'name = "OpenAI"\n'
        'base_url = "https://api.openai.com/v1"\n'
    )
    assert configure.select_retained_provider(text) == "ezyhub"


def _write(p, s):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8")


def test_merge_config_retains_ezyapis_inline_token_no_env_key(tmp_path):
    configure = load_configure_helper()
    home = tmp_path / "codex"
    _write(home / "config.toml",
        'model_provider = "ezyapis"\n'
        'model = "gpt-5.6-sol"\n\n'
        '[model_providers.ezyapis]\n'
        'name = "EzyHub"\n'
        'base_url = "https://api.ezyapis.com/v1"\n'
        'wire_api = "responses"\n'
        'env_key = "EZYHUB_CODEX_KEY"\n'
    )
    configure.merge_config(home, "https://api.ezyapis.com/v1", "gpt-5.6-sol", "sk-ezyhub-jane-XYZ")
    text = (home / "config.toml").read_text()
    assert 'model_provider = "ezyapis"' in text          # retained, not renamed
    assert '[model_providers.ezyapis]' in text
    assert '[model_providers.ezyhub]' not in text
    assert 'experimental_bearer_token = "sk-ezyhub-jane-XYZ"' in text
    assert 'requires_openai_auth = true' in text
    # env_key removed from the active provider section
    active = text.split('[model_providers.ezyapis]')[1].split('[')[0]
    assert 'env_key' not in active
    assert 'image_generation = false' in text
    assert 'model = "gpt-5.6-sol"' in text


def test_merge_config_new_install_uses_ezyhub(tmp_path):
    configure = load_configure_helper()
    home = tmp_path / "codex"
    configure.merge_config(home, "https://api.ezyapis.com/v1", "gpt-5.6-sol", "sk-ezyhub-new-1")
    text = (home / "config.toml").read_text()
    assert 'model_provider = "ezyhub"' in text
    assert '[model_providers.ezyhub]' in text
    assert 'experimental_bearer_token = "sk-ezyhub-new-1"' in text
    assert 'requires_openai_auth = true' in text
    assert 'wire_api = "responses"' in text


def test_merge_config_preserves_unrelated_and_backs_up(tmp_path):
    configure = load_configure_helper()
    home = tmp_path / "codex"
    _write(home / "config.toml",
        'model_provider = "ezyhub"\n'
        '[features]\n'
        'web_search = true\n\n'
        '[mcp_servers.personal]\n'
        'url = "http://localhost:9/mcp"\n\n'
        '[model_providers.ezyhub]\n'
        'name = "EzyHub"\n'
        'base_url = "https://api.ezyapis.com/v1"\n'
        'wire_api = "responses"\n'
    )
    configure.merge_config(home, "https://api.ezyapis.com/v1", "gpt-5.6-sol", "sk-ezyhub-2")
    text = (home / "config.toml").read_text()
    assert '[mcp_servers.personal]' in text            # untouched
    assert 'web_search = true' in text                 # other feature flag kept
    assert 'image_generation = false' in text          # merged in
    backups = list(home.glob("config.toml.ezyhub-bak-*"))
    assert len(backups) == 1                            # timestamped backup created


def test_merge_config_backup_is_0600(tmp_path):
    configure = load_configure_helper()
    home = tmp_path / "codex"
    _write(home / "config.toml",
        'model_provider = "ezyapis"\n'
        '[model_providers.ezyapis]\n'
        'name = "EzyHub"\n'
        'base_url = "https://api.ezyapis.com/v1"\n'
        'wire_api = "responses"\n'
        'experimental_bearer_token = "sk-ezyhub-prev-live"\n'
    )
    configure.merge_config(home, "https://api.ezyapis.com/v1", "gpt-5.6-sol", "sk-ezyhub-4")
    backups = list(home.glob("config.toml.ezyhub-bak-*"))
    assert len(backups) == 1
    # backup holds the OLD config (a live inline token) -> must never be world/group readable
    assert (backups[0].stat().st_mode & 0o777) == 0o600


def test_merge_config_idempotent(tmp_path):
    configure = load_configure_helper()
    home = tmp_path / "codex"
    configure.merge_config(home, "https://api.ezyapis.com/v1", "gpt-5.6-sol", "sk-ezyhub-3")
    first = (home / "config.toml").read_text()
    configure.merge_config(home, "https://api.ezyapis.com/v1", "gpt-5.6-sol", "sk-ezyhub-3")
    second = (home / "config.toml").read_text()
    # no duplicate provider/features sections on a second run
    assert second.count('[model_providers.ezyhub]') == 1
    assert second.count('image_generation = false') == 1
    assert second.count('requires_openai_auth = true') == 1


def test_merge_config_dedupes_foreign_retained_provider_section(tmp_path):
    import tomllib

    configure = load_configure_helper()
    home = tmp_path / "codex"
    _write(home / "config.toml",
        'model_provider = "work"\n'
        'model = "gpt-5.6-sol"\n\n'
        '[model_providers.work]\n'
        'name = "EzyHub"\n'
        'base_url = "https://api.ezyapis.com/v1"\n'
        'wire_api = "responses"\n'
        'env_key = "EZYHUB_CODEX_KEY"\n'
    )
    configure.merge_config(home, "https://api.ezyapis.com/v1", "gpt-5.6-sol", "sk-ezyhub-w-1")
    text = (home / "config.toml").read_text()
    assert 'model_provider = "work"' in text               # retained, not renamed
    assert text.count("[model_providers.work]") == 1       # no duplicate table
    assert "env_key" not in text                           # stale line gone
    parsed = tomllib.loads(text)
    assert parsed["model_providers"]["work"]["experimental_bearer_token"] == "sk-ezyhub-w-1"


def test_merge_config_dedupes_foreign_id_owned_by_gateway_baseurl(tmp_path):
    import tomllib

    configure = load_configure_helper()
    home = tmp_path / "codex"
    _write(home / "config.toml",
        'model_provider = "work"\n\n'
        '[model_providers.work]\n'
        'name = "Work Gateway"\n'
        'base_url = "https://api.ezyapis.com/v1/"\n'
    )
    configure.merge_config(home, "https://api.ezyapis.com/v1", "gpt-5.6-sol", "sk-ezyhub-w-2")
    text = (home / "config.toml").read_text()
    assert 'model_provider = "work"' in text
    assert text.count("[model_providers.work]") == 1
    tomllib.loads(text)


def test_merge_config_features_header_with_trailing_comment(tmp_path):
    import tomllib

    configure = load_configure_helper()
    home = tmp_path / "codex"
    _write(home / "config.toml",
        "[features] # flags\n"
        "web_search = true\n"
    )
    configure.merge_config(home, "https://api.ezyapis.com/v1", "gpt-5.6-sol", "sk-ezyhub-f-1")
    text = (home / "config.toml").read_text()
    assert text.count("[features]") == 1
    assert "image_generation = false" in text
    assert "web_search = true" in text
    tomllib.loads(text)


def test_merge_config_retains_provider_despite_trailing_comments(tmp_path):
    configure = load_configure_helper()
    home = tmp_path / "codex"
    _write(home / "config.toml",
        'model_provider = "ezyapis" # company gateway\n\n'
        '[model_providers.ezyapis]\n'
        'name = "EzyHub"\n'
        'base_url = "https://api.ezyapis.com/v1" # gateway\n'
        'wire_api = "responses"\n'
    )
    configure.merge_config(home, "https://api.ezyapis.com/v1", "gpt-5.6-sol", "sk-ezyhub-c-1")
    text = (home / "config.toml").read_text()
    assert 'model_provider = "ezyapis"' in text            # retained, NOT migrated to ezyhub
    assert "[model_providers.ezyhub]" not in text


def test_select_retained_provider_tolerates_trailing_comment_on_foreign_id(tmp_path):
    configure = load_configure_helper()
    text = (
        'model_provider = "work" # gateway\n\n'
        "[model_providers.work]\n"
        'name = "EzyHub" # ours\n'
    )
    assert configure.select_retained_provider(text) == "work"


def test_merge_config_toml_escapes_inline_token(tmp_path):
    import tomllib

    configure = load_configure_helper()
    home = tmp_path / "codex"
    tricky_key = 'sk-ezyhub-qu"ote\\slash'
    configure.merge_config(home, "https://api.ezyapis.com/v1", "gpt-5.6-sol", tricky_key)
    text = (home / "config.toml").read_text()
    parsed = tomllib.loads(text)
    assert parsed["model_providers"]["ezyhub"]["experimental_bearer_token"] == tricky_key


def test_key_rotate_configures_new_key_with_fixed_model(monkeypatch):
    helper = load_helper()
    seen: dict = {}
    rotate_calls: list[tuple[str, str, str | None]] = []

    def fake_request_json(method, path, **kwargs):
        rotate_calls.append((method, path, kwargs.get("token")))
        return {"key": "sk-ezyhub-new"}

    monkeypatch.setattr(helper, "read_codex_key", lambda: "sk-ezyhub-old")
    monkeypatch.setattr(helper, "request_json", fake_request_json)
    monkeypatch.setattr(
        helper,
        "configure_codex_with_key",
        lambda key, base_url, model: seen.update(key=key, base_url=base_url, model=model),
    )

    helper.cmd_key_rotate(
        argparse.Namespace(backend_url="https://b", base_url="https://api.ezyapis.com/v1", model="gpt-5.6-sol")
    )

    # old key travels as bearer token (never in the URL path)
    assert rotate_calls == [("POST", "/keys/rotate", "sk-ezyhub-old")]
    # new key flows through the mixed-auth configure path (inline token + .env)
    assert seen == {"key": "sk-ezyhub-new", "base_url": "https://api.ezyapis.com/v1", "model": "gpt-5.6-sol"}


def test_key_rotate_parser_defaults_to_gpt_56_sol(monkeypatch):
    helper = load_helper()
    monkeypatch.setattr(sys, "argv", ["ezyhub_backend.py", "key-rotate"])
    args = helper.parse_args()
    assert args.model == "gpt-5.6-sol"
    assert args.base_url == helper.DEFAULT_GATEWAY_BASE_URL


def _doctor_args(**overrides):
    base = dict(backend_url="https://backend", kb_health_url="https://kb/health", no_kb=False, admin_key=None)
    base.update(overrides)
    return argparse.Namespace(**base)


def test_doctor_kb_mcp_skipped_when_unprovisioned(monkeypatch):
    helper = load_helper()
    monkeypatch.setattr(helper, "request_json", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(
        helper,
        "request_json_url",
        lambda url: {
            "ok": True,
            "ready": False,
            "configured": {
                "ezyhub_api_key": False,
                "knowledge_base_ids": False,
                "mcp_bearer_token": False,
                "workspace_id": False,
            },
        },
    )
    monkeypatch.delenv("EZYHUB_ADMIN_KEY", raising=False)
    report = helper.build_doctor_report(_doctor_args())
    kb = report["checks"]["kb_mcp"]
    assert kb["skipped"] is True
    assert kb["ok"] is False
    assert "not provisioned" in kb["reason"]


def test_doctor_kb_mcp_fails_when_partially_configured(monkeypatch):
    helper = load_helper()
    monkeypatch.setattr(helper, "request_json", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(
        helper,
        "request_json_url",
        lambda url: {
            "ok": True,
            "ready": False,
            "configured": {
                "ezyhub_api_key": True,
                "knowledge_base_ids": False,
                "mcp_bearer_token": True,
                "workspace_id": True,
            },
        },
    )
    monkeypatch.delenv("EZYHUB_ADMIN_KEY", raising=False)
    report = helper.build_doctor_report(_doctor_args())
    kb = report["checks"]["kb_mcp"]
    assert kb["ok"] is False
    assert not kb.get("skipped")


def test_doctor_kb_mcp_ok_when_ready(monkeypatch):
    helper = load_helper()
    monkeypatch.setattr(helper, "request_json", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(helper, "request_json_url", lambda url: {"ok": True, "ready": True})
    monkeypatch.delenv("EZYHUB_ADMIN_KEY", raising=False)
    report = helper.build_doctor_report(_doctor_args())
    assert report["checks"]["kb_mcp"]["ok"] is True
