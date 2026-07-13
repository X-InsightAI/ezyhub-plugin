#!/usr/bin/env python3
"""Configure Codex App/Core for the EzyHub company gateway.

Production enrollment passes the key issued by the Google Workspace enroll
backend to this configuration path. Development callers must pass an explicit
key through a prompt, secret file, or environment variable; existing local
client keys are not a valid source of truth for company enrollment.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path


DEFAULT_BASE_URL = "https://api.ezyapis.com/v1"
DEFAULT_MODEL = "gpt-5.6-sol"
ENV_FILE_NAME = ".env"
CODEX_KEY_ENV_VAR = "EZYHUB_CODEX_KEY"
PROVIDER_NAME = "ezyhub"
MANAGED_PROVIDER_NAMES = (PROVIDER_NAME, "company", "ezyapis")


def resolve_key(args: argparse.Namespace) -> str:
    if args.from_wsl_cliproxy:
        raise RuntimeError(
            "reading an existing WSL CLIProxyAPI client key is disabled; "
            "use backend enroll so key-backend issues a new managed key"
        )
    if args.key:
        return args.key.strip()
    if args.key_env:
        value = os.environ.get(args.key_env, "").strip()
        if not value:
            raise RuntimeError(f"environment variable {args.key_env} is empty")
        return value
    value = os.environ.get("EZYHUB_CODEX_KEY", "").strip()
    if value:
        return value
    raise RuntimeError("no key provided; use backend enroll, --key-env, --key, or EZYHUB_CODEX_KEY")


def strip_managed_provider_sections(text: str) -> str:
    provider_names = "|".join(re.escape(name) for name in MANAGED_PROVIDER_NAMES)
    pattern = re.compile(
        rf"(?ms)^\[model_providers\.({provider_names})\]\n.*?(?=^\[[^\n]+\]\n|\Z)"
    )
    return pattern.sub("", text).rstrip() + "\n"


def strip_provider_section(text: str, provider_id: str) -> str:
    pattern = re.compile(
        rf"(?ms)^\[model_providers\.{re.escape(provider_id)}\]\n.*?(?=^\[[^\n]+\]\n|\Z)"
    )
    return pattern.sub("", text)


def _top_level_value(text: str, key: str) -> str | None:
    m = re.search(rf'(?m)^{re.escape(key)}\s*=\s*"([^"]*)"\s*(?:#.*)?$', text)
    return m.group(1) if m else None


def _provider_section(text: str, provider_id: str) -> str | None:
    m = re.search(
        rf"(?ms)^\[model_providers\.{re.escape(provider_id)}\]\n(.*?)(?=^\[[^\n]+\]\n|\Z)",
        text,
    )
    return m.group(1) if m else None


def _section_field(section: str, field: str) -> str | None:
    m = re.search(rf'(?m)^\s*{re.escape(field)}\s*=\s*"([^"]*)"\s*(?:#.*)?$', section)
    return m.group(1) if m else None


def select_retained_provider(text: str) -> str:
    active = _top_level_value(text, "model_provider")
    if not active:
        return PROVIDER_NAME
    if active in MANAGED_PROVIDER_NAMES:
        return active
    section = _provider_section(text, active)
    if section is None:
        return PROVIDER_NAME
    if _section_field(section, "name") == "EzyHub":
        return active
    base = _section_field(section, "base_url")
    if base and base.rstrip("/") == DEFAULT_BASE_URL.rstrip("/"):
        return active
    return PROVIDER_NAME


def set_top_level_string(text: str, key: str, value: str) -> str:
    line = f'{key} = "{value}"'
    pattern = re.compile(rf"(?m)^{re.escape(key)}\s*=\s*.*$")
    if pattern.search(text):
        return pattern.sub(line, text, count=1)
    return line + "\n" + text


def dotenv_quote(value: str) -> str:
    if any(char in value for char in "\r\n\0"):
        raise ValueError("dotenv values cannot contain newline or NUL")
    safe = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./:@%+=,-"
    if value and all(char in safe for char in value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "$$")
    return f'"{escaped}"'


def update_env_text(content: str, updates: dict[str, str]) -> str:
    lines = content.splitlines()
    seen: set[str] = set()
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            output.append(line)
            continue
        name = line.split("=", 1)[0].strip()
        if name in updates:
            output.append(f"{name}={dotenv_quote(updates[name])}")
            seen.add(name)
        else:
            output.append(line)
    for name, value in updates.items():
        if name not in seen:
            output.append(f"{name}={dotenv_quote(value)}")
    return "\n".join(output).rstrip() + "\n"


def write_codex_env_key(codex_home: Path, key: str) -> Path:
    env_path = codex_home / ENV_FILE_NAME
    text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    updated = update_env_text(text, {CODEX_KEY_ENV_VAR: key})
    env_path.parent.mkdir(parents=True, exist_ok=True)
    old_umask = os.umask(0o177)
    try:
        env_path.write_text(updated, encoding="utf-8")
    finally:
        os.umask(old_umask)
    try:
        env_path.chmod(0o600)
    except PermissionError:
        pass
    return env_path


def remove_previous_ezyhub_auth_key(codex_home: Path, key: str) -> bool:
    auth_path = codex_home / "auth.json"
    if not auth_path.exists():
        return False
    try:
        payload = json.loads(auth_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    existing = payload.get("OPENAI_API_KEY")
    if not isinstance(existing, str):
        return False
    if existing != key and not existing.startswith("sk-ezyhub-"):
        return False
    payload.pop("OPENAI_API_KEY", None)
    if payload.get("auth_mode") == "apikey":
        payload.pop("auth_mode", None)
    auth_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        auth_path.chmod(0o600)
    except PermissionError:
        pass
    return True


def merge_features_image_off(text: str) -> str:
    m = re.search(r"(?ms)^\[features\][ \t]*(?:#[^\n]*)?\n(.*?)(?=^\[[^\n]+\]\n|\Z)", text)
    if m is None:
        block = "[features]\nimage_generation = false\n"
        return text.rstrip() + "\n\n" + block
    body = m.group(1)
    if re.search(r"(?m)^\s*image_generation\s*=", body):
        body = re.sub(r"(?m)^\s*image_generation\s*=.*$", "image_generation = false", body, count=1)
    else:
        body = body.rstrip() + "\nimage_generation = false\n"
    return text[: m.start(1)] + body + text[m.end(1):]


def toml_escape_basic(value: str) -> str:
    if any(char in value for char in "\r\n"):
        raise ValueError("TOML string values cannot contain newlines")
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _render_provider(provider_id: str, base_url: str, key: str) -> str:
    return (
        f"\n\n[model_providers.{provider_id}]\n"
        f'name = "EzyHub"\n'
        f'base_url = "{base_url.rstrip("/")}"\n'
        f'wire_api = "responses"\n'
        f'experimental_bearer_token = "{toml_escape_basic(key)}"\n'
        f"requires_openai_auth = true\n"
    )


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    old_umask = os.umask(0o177)
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        os.umask(old_umask)
    try:
        path.chmod(0o600)
    except PermissionError:
        pass


def merge_config(codex_home: Path, base_url: str, model: str, key: str) -> Path:
    config_path = codex_home / "config.toml"
    text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    if config_path.exists():
        backup = config_path.with_name(
            config_path.name + ".ezyhub-bak-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        )
        _atomic_write(backup, text)
    provider_id = select_retained_provider(text)
    text = strip_managed_provider_sections(text)  # drop ALL managed sections; we re-add the retained one clean
    # The retained id may be foreign (owned via name/base_url, e.g. "work"); its old
    # section must also go so the freshly rendered table below is the only one.
    text = strip_provider_section(text, provider_id)
    text = set_top_level_string(text, "model_provider", provider_id)
    text = set_top_level_string(text, "model", model)
    text = merge_features_image_off(text)
    text = text.rstrip() + _render_provider(provider_id, base_url, key)
    _atomic_write(config_path, text.rstrip() + "\n")
    return config_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Configure Codex for EzyHub company gateway.")
    parser.add_argument("--from-wsl-cliproxy", action="store_true", help="Deprecated; disabled because WSL is only a local client, not a key source.")
    parser.add_argument("--key-env", help="Environment variable containing the CLIProxyAPI key.")
    parser.add_argument("--key", help="CLIProxyAPI key. Avoid this in shell history outside local development.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Company OpenAI-compatible base URL.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Default model name.")
    parser.add_argument("--codex-home", default=os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    codex_home = Path(args.codex_home).expanduser().resolve()
    try:
        key = resolve_key(args)
        env_path = write_codex_env_key(codex_home, key)
        removed_auth_key = remove_previous_ezyhub_auth_key(codex_home, key)
        config_path = merge_config(codex_home, args.base_url, args.model, key)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Configured EzyHub mixed-auth provider in {config_path}")
    print(f"Stored EzyHub key in {env_path} as {CODEX_KEY_ENV_VAR} (hidden).")
    if removed_auth_key:
        print("Removed previous EzyHub OPENAI_API_KEY from Codex auth.json.")
    print("Open a new Codex App thread to pick up the updated provider/key.")
    print("If the new thread is stale, quit and reopen Codex App.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
