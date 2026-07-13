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
from pathlib import Path


DEFAULT_BASE_URL = "https://api.ezyapis.com/v1"
DEFAULT_MODEL = "gpt-5.5"
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


def strip_managed_inline_tokens(text: str) -> str:
    provider_names = "|".join(re.escape(name) for name in MANAGED_PROVIDER_NAMES)
    section_pattern = re.compile(
        rf"(?ms)^(\[model_providers\.({provider_names})\]\n)(.*?)(?=^\[[^\n]+\]\n|\Z)"
    )
    token_pattern = re.compile(r"(?m)^\s*experimental_bearer_token\s*=.*(?:\n|$)")

    def replace(match: re.Match[str]) -> str:
        header = match.group(1)
        body = token_pattern.sub("", match.group(3)).rstrip()
        if body:
            return header + body + "\n"
        return header

    return section_pattern.sub(replace, text)


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


def merge_config(codex_home: Path, base_url: str, model: str) -> Path:
    config_path = codex_home / "config.toml"
    text = config_path.read_text() if config_path.exists() else ""
    text = strip_managed_inline_tokens(text)
    text = strip_managed_provider_sections(text)
    text = set_top_level_string(text, "model_provider", PROVIDER_NAME)
    text = set_top_level_string(text, "model", model)
    provider = f"""

[model_providers.{PROVIDER_NAME}]
name = "EzyHub"
base_url = "{base_url.rstrip('/')}"
wire_api = "responses"
env_key = "{CODEX_KEY_ENV_VAR}"
"""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(text.rstrip() + provider, encoding="utf-8")
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
        config_path = merge_config(codex_home, args.base_url, args.model)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Configured Codex provider '{PROVIDER_NAME}' in {config_path}")
    print(f"Stored EzyHub key in {env_path} as {CODEX_KEY_ENV_VAR} (hidden).")
    if removed_auth_key:
        print("Removed previous EzyHub OPENAI_API_KEY from Codex auth.json.")
    print("Open a new Codex App thread to pick up the updated provider/key.")
    print("If the new thread is stale, quit and reopen Codex App.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
