#!/usr/bin/env python3
"""Interact with the EzyHub Codex key backend from plugin skills."""

from __future__ import annotations

import argparse
import base64
import getpass
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any


DEFAULT_BACKEND_URL = "https://codex.ezyapis.com"
DEFAULT_KB_HEALTH_URL = "https://kb.ezyapis.com/health"
DEFAULT_PUBLIC_GATEWAY_BASE_URL = "https://api.ezyapis.com/v1"
DEFAULT_GATEWAY_BASE_URL = DEFAULT_PUBLIC_GATEWAY_BASE_URL
EZYHUB_PROVIDER_NAME = "ezyhub"
COMPANY_SKILL_PREFIX = "ezyhub-"
MANIFEST_NAME = ".ezyhub-skills.json"
CODEX_APP_KB_TOKEN_ENV = "EZYHUB_KB_MCP_TOKEN"
CODEX_CLIENT_KEY_ENV = "EZYHUB_CODEX_KEY"
CODEX_ENV_FILE_NAME = ".env"
HELPER_PYTHON = "python" if os.name == "nt" else "python3"
HELPER_COMMAND = f"{HELPER_PYTHON} plugins/ezyhub/scripts/ezyhub_backend.py"
AUTO_SYNC_MARKER = "ezyhub-codex-auto-sync"
LAUNCHD_LABEL = "com.ezyhub.codex-auto-sync"
SCHTASKS_TASK_NAME = "EzyHubCodexAutoSync"


SECTION_RE = re.compile(r"^\s*\[([A-Za-z0-9_.-]+)\]\s*(?:#.*)?$")
STRING_VALUE_RE = re.compile(r'^\s*([A-Za-z0-9_.-]+)\s*=\s*"((?:[^"\\]|\\.)*)"\s*(?:#.*)?$')
BOOL_VALUE_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*=\s*(true|false)\s*(?:#.*)?$")


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()


def parse_dotenv_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].strip()
        name, value = stripped.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = bytes(value[1:-1], "utf-8").decode("unicode_escape")
        elif len(value) >= 2 and value[0] == value[-1] == "'":
            value = value[1:-1]
        values[name] = value
    return values


def codex_env_values(home: Path | None = None) -> dict[str, str]:
    home = home or codex_home()
    return parse_dotenv_values(home / CODEX_ENV_FILE_NAME)


def read_codex_key() -> str:
    home = codex_home()
    env_values = codex_env_values(home)
    # 1. EZYHUB_CODEX_KEY read directly from CODEX_HOME/.env — the persisted source
    #    of truth that enroll/key-rotate write. We deliberately do NOT read an
    #    inherited process env var: a long-running Codex App can carry a stale key
    #    that would otherwise outrank the freshly enrolled one in .env.
    value = env_values.get(CODEX_CLIENT_KEY_ENV, "").strip()
    if value:
        return value
    # 2. active provider inline experimental_bearer_token (only when the active
    #    provider is EzyHub-owned; a foreign provider's token is a third-party
    #    secret and must never be forwarded to the EzyHub backend)
    config_path = home / "config.toml"
    if config_path.exists():
        try:
            config_text = config_path.read_text(encoding="utf-8")
            config_payload = parse_codex_config_strings(config_text)
            providers = config_payload.get("model_providers")
            providers = providers if isinstance(providers, dict) else {}
            model_provider = config_payload.get("model_provider")
            if ezyhub_owned_active_provider(config_text, model_provider):
                active = providers.get(model_provider) if isinstance(model_provider, str) else None
                if isinstance(active, dict):
                    token = active.get("experimental_bearer_token")
                    if isinstance(token, str) and token.strip():
                        return token.strip()
        except Exception:
            pass
    # 3. legacy auth.json OPENAI_API_KEY
    auth_path = home / "auth.json"
    try:
        payload = json.loads(auth_path.read_text())
    except FileNotFoundError as exc:
        raise RuntimeError("EzyHub key not found; run /enroll first") from exc
    key = payload.get("OPENAI_API_KEY")
    if not isinstance(key, str) or not key:
        raise RuntimeError("EzyHub key not found; run /enroll first")
    return key


def request_json(
    method: str,
    path: str,
    *,
    backend_url: str,
    token: str | None = None,
    extra_headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    query: dict[str, str] | None = None,
) -> dict[str, Any]:
    url = backend_url.rstrip("/") + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = None
    headers = {"Accept": "application/json", "User-Agent": "ezyhub-codex-plugin/0.1.0"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"{method} {path} failed: HTTP {exc.code} {detail}") from exc


def request_json_url(url: str) -> dict[str, Any]:
    headers = {"Accept": "application/json", "User-Agent": "ezyhub-codex-plugin/0.1.0"}
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"GET {url} failed: HTTP {exc.code} {detail}") from exc


def load_configure_codex_module():
    script = Path(__file__).with_name("ezyhub_configure_codex.py")
    spec = importlib.util.spec_from_file_location("_ezyhub_configure_codex", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ezyhub_owned_active_provider(config_text: str, model_provider: Any) -> bool:
    """True when the active model_provider is EzyHub-owned (managed id, name EzyHub, or gateway base_url)."""
    if not isinstance(model_provider, str) or not model_provider:
        return False
    try:
        configure = load_configure_codex_module()
        return configure.select_retained_provider(config_text) == model_provider
    except Exception:
        return False


def configure_codex_with_key(key: str, base_url: str, model: str) -> None:
    script = Path(__file__).with_name("ezyhub_configure_codex.py")
    env = os.environ.copy()
    env["EZYHUB_CODEX_KEY"] = key
    result = subprocess.run(
        [sys.executable, str(script), "--base-url", base_url, "--model", model],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Codex configuration failed")
    print(result.stdout.rstrip())


def read_optional_secret(*, env_var: str, secret_file: str | None, prompt: str | None) -> str | None:
    if secret_file:
        return Path(secret_file).expanduser().read_text(encoding="utf-8").strip()
    value = os.environ.get(env_var, "").strip()
    if value:
        return value
    if prompt:
        value = getpass.getpass(prompt).strip()
        return value or None
    return None


def read_codex_app_kb_token(args: argparse.Namespace) -> str:
    token = read_optional_secret(
        env_var=CODEX_APP_KB_TOKEN_ENV,
        secret_file=args.token_file,
        prompt="Codex App KB MCP token: " if args.prompt_token else None,
    )
    if not token:
        token = getpass.getpass("Codex App KB MCP token: ").strip()
    if not token:
        raise RuntimeError("Codex App KB MCP token is required")
    return token


def read_codex_client_key(args: argparse.Namespace) -> str:
    key = read_optional_secret(
        env_var=CODEX_CLIENT_KEY_ENV,
        secret_file=args.key_file,
        prompt="CLIProxyAPI client key: " if args.prompt_key else None,
    )
    if not key:
        key = getpass.getpass("CLIProxyAPI client key: ").strip()
    if not key:
        raise RuntimeError("CLIProxyAPI client key is required")
    if key.startswith(("$2a$", "$2b$", "$2y$")):
        raise RuntimeError(
            "this looks like the CLIProxyAPI management key's bcrypt management hash, not a "
            "plaintext key; Codex App needs an employee/client key"
        )
    if not key.startswith("sk-"):
        raise RuntimeError(
            "this does not look like a CLIProxyAPI employee/client key; "
            "do not configure a management key in Codex App"
        )
    return key


def run_checked(command: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"command failed: {command[0]}"
        raise RuntimeError(detail)
    return result


def launchctl_setenv(name: str, value: str) -> None:
    result = run_checked(["launchctl", "setenv", name, value])
    if result.stdout.strip():
        print(result.stdout.strip())


def launchctl_unsetenv(name: str) -> None:
    result = run_checked(["launchctl", "unsetenv", name])
    if result.stdout.strip():
        print(result.stdout.strip())


def launchctl_getenv(name: str) -> str | None:
    result = subprocess.run(
        ["launchctl", "getenv", name],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def admin_headers(args: argparse.Namespace) -> dict[str, str]:
    key = args.admin_key or os.environ.get("EZYHUB_ADMIN_KEY", "")
    if not key:
        raise RuntimeError("EZYHUB_ADMIN_KEY or --admin-key is required for admin commands")
    return {"X-Ezyhub-Admin-Key": key}


def cmd_status(args: argparse.Namespace) -> None:
    payload = request_json("GET", "/me", backend_url=args.backend_url, token=read_codex_key())
    print(f"EzyHub Codex status: {payload.get('status')}")
    print(f"Email: {payload.get('google_email')}")
    print(f"Name: {payload.get('name') or '-'}")
    print(f"Role: {payload.get('role')}")
    usage = payload.get("usage")
    if isinstance(usage, dict):
        print("Usage:")
        print(json.dumps(usage, indent=2, sort_keys=True))
    else:
        print("Usage: unavailable")
    print("Key: configured (hidden)")


def load_manifest(skills_dir: Path) -> dict[str, Any]:
    path = skills_dir / MANIFEST_NAME
    empty: dict[str, Any] = {"managed": [], "mcp_servers": [], "managed_files": {}}
    if not path.exists():
        return empty
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError:
        return empty
    if not isinstance(payload, dict):
        return empty
    managed = [n for n in payload.get("managed", []) if isinstance(n, str)]
    raw_files = payload.get("managed_files")
    managed_files: dict[str, list[str]] = {}
    if isinstance(raw_files, dict):
        for name, paths in raw_files.items():
            if isinstance(name, str) and isinstance(paths, list):
                managed_files[name] = [p for p in paths if isinstance(p, str)]
    for name in managed:
        managed_files.setdefault(name, [])
    return {"managed": managed, "mcp_servers": [n for n in payload.get("mcp_servers", []) if isinstance(n, str)], "managed_files": managed_files}


def write_manifest(
    skills_dir: Path,
    names: list[str],
    mcp_names: list[str] | None = None,
    managed_files: dict[str, list[str]] | None = None,
) -> None:
    (skills_dir / MANIFEST_NAME).write_text(
        json.dumps(
            {
                "managed": sorted(names),
                "mcp_servers": sorted(mcp_names or []),
                "managed_files": {name: sorted(paths) for name, paths in sorted((managed_files or {}).items())},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


# Matches the managed server's own `[mcp_servers.<name>]` header (tolerant of trailing
# whitespace/comments) plus any immediately-following `[mcp_servers.<name>.*]` subtables,
# stopping at the first header that belongs to a different server (or end of file).
MCP_SECTION_RE_TEMPLATE = (
    r"(?ms)^\[mcp_servers\.{name}(?:\.[^\]\n]+)?\][ \t]*(?:#[^\n]*)?\n"
    r".*?(?=^\[(?!mcp_servers\.{name}[.\]])|\Z)"
)


def _render_mcp_section(server: dict[str, Any]) -> str:
    lines = [f"[mcp_servers.{server['name']}]", f'url = "{server["url"]}"']
    token_var = server.get("bearer_token_env_var")
    if token_var:
        lines.append(f'bearer_token_env_var = "{token_var}"')
    return "\n".join(lines) + "\n"


def _has_unsafe_toml_chars(value: str) -> bool:
    return '"' in value or "\\" in value or "\n" in value


def _replace_first_and_blank_rest(pattern: "re.Pattern[str]", replacement: str, text: str) -> str:
    """Replace the first regex match with a literal string; blank out any further matches.

    Always uses a lambda repl (never a raw string) so `replacement` is never interpreted
    for backreferences (``\\g<0>``, ``\\1``, ...) by re.sub.
    """
    state = {"used": False}

    def _sub(match: "re.Match[str]") -> str:
        if state["used"]:
            return ""
        state["used"] = True
        return replacement

    return pattern.sub(_sub, text)


def apply_mcp_servers(home: Path, servers: list[dict[str, Any]], previous: set[str]) -> list[str]:
    config_path = home / "config.toml"
    text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    current: list[str] = []
    for server in servers:
        name = server.get("name")
        url = server.get("url")
        token_var = server.get("bearer_token_env_var")
        if not isinstance(name, str) or not re.fullmatch(r"ezyhub-[A-Za-z0-9_-]+", name):
            raise RuntimeError(f"refusing to manage non-company MCP server name: {name}")
        if not isinstance(url, str) or not url:
            raise RuntimeError(f"MCP server {name} has no url")
        for label, value in (("url", url), ("bearer_token_env_var", token_var)):
            if isinstance(value, str) and _has_unsafe_toml_chars(value):
                raise RuntimeError(f"MCP server {name} has an unsafe {label} value")
        if server.get("enabled") is False:
            continue
        section = _render_mcp_section(server)
        pattern = re.compile(MCP_SECTION_RE_TEMPLATE.format(name=re.escape(name)))
        if pattern.search(text):
            text = _replace_first_and_blank_rest(pattern, section.rstrip("\n") + "\n\n", text)
        else:
            if text and not text.endswith("\n\n"):
                text = text.rstrip("\n") + "\n\n"
            text += section
        current.append(name)
    for removed in sorted(previous - set(current)):
        if not removed.startswith(COMPANY_SKILL_PREFIX):
            continue
        pattern = re.compile(MCP_SECTION_RE_TEMPLATE.format(name=re.escape(removed)))
        text = pattern.sub(lambda match: "", text)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(text, encoding="utf-8")
    return sorted(current)


# Bundle caps and path validation mirror the key backend's skillfs module (kept
# inline because this helper is stdlib-only and must not import backend code).
BUNDLE_MAX_FILES = 20
BUNDLE_MAX_BYTES = 5 * 1024 * 1024
# Server-synced role bundles use the backend's larger role-skill caps
# (skillfs.ROLE_BUNDLE_MAX_*); the tight caps above stay for employee uploads.
ROLE_BUNDLE_MAX_FILES = 120
ROLE_BUNDLE_MAX_BYTES = 25 * 1024 * 1024
FILE_MAX_BYTES = 1 * 1024 * 1024
# Managed skill dir names: used by both the write gate and the removal loop.
COMPANY_SKILL_NAME_RE = re.compile(r"ezyhub-[A-Za-z0-9._-]+")


def validate_rel_path(path: str) -> None:
    if not path or path.startswith("/"):
        raise ValueError(f"invalid path: {path!r}")
    parts = path.replace("\\", "/").split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"invalid path segment in: {path!r}")
    normalized = os.path.normpath(path)
    if normalized.startswith("..") or os.path.isabs(normalized):
        raise ValueError(f"escaping path: {path!r}")


def decode_bundle_file(f: dict) -> tuple[str, bytes]:
    path = f.get("path")
    if not isinstance(path, str):
        raise ValueError("file missing path")
    validate_rel_path(path)
    enc = f.get("encoding", "utf-8")
    content = f.get("content", "")
    if enc == "base64":
        data = base64.b64decode(content)
    elif enc == "utf-8":
        data = content.encode("utf-8")
    else:
        raise ValueError(f"unknown encoding: {enc}")
    if len(data) > FILE_MAX_BYTES:
        raise ValueError(f"file exceeds size cap: {path}")
    return path, data


def _is_binary(data: bytes) -> bool:
    if b"\x00" in data:
        return True
    try:
        data.decode("utf-8")
        return False
    except UnicodeDecodeError:
        return True


def read_local_skill_tree(skill_dir: Path) -> list[dict]:
    """Read a local skill directory into a bundle payload.

    Mirrors the backend's skillfs.read_skill_bundle safety: rejects symlinks and
    traversal-shaped relative paths, and enforces the same file/byte caps, so an
    unsafe tree is refused locally before anything is uploaded.
    """
    files: list[dict] = []
    total = 0
    for p in sorted(skill_dir.rglob("*")):
        rel = p.relative_to(skill_dir).as_posix()
        if p.is_symlink():
            raise ValueError(f"symlink not allowed in bundle: {rel}")
        if not p.is_file():
            continue
        validate_rel_path(rel)
        data = p.read_bytes()
        if len(data) > FILE_MAX_BYTES:
            raise ValueError(f"file exceeds size cap: {rel}")
        total += len(data)
        if len(files) >= BUNDLE_MAX_FILES or total > BUNDLE_MAX_BYTES:
            raise ValueError("bundle exceeds file/byte cap")
        if _is_binary(data):
            files.append({"path": rel, "content": base64.b64encode(data).decode("ascii"), "encoding": "base64"})
        else:
            files.append({"path": rel, "content": data.decode("utf-8"), "encoding": "utf-8"})
    if not files:
        raise ValueError(f"skill directory has no files: {skill_dir}")
    return files


def cmd_publish_skill(args: argparse.Namespace) -> None:
    skill_dir = Path(args.dir).expanduser()
    if not skill_dir.is_dir():
        raise RuntimeError(f"skill directory does not exist: {skill_dir}")
    name = args.name or skill_dir.resolve().name
    if not name.startswith(COMPANY_SKILL_PREFIX):
        raise RuntimeError(
            f"skill name must start with {COMPANY_SKILL_PREFIX}: {name!r} (pass --name {COMPANY_SKILL_PREFIX}<slug>)"
        )
    files = read_local_skill_tree(skill_dir)
    payload = request_json(
        "POST",
        "/skills/submit",
        backend_url=args.backend_url,
        token=read_codex_key(),
        body={"skill_name": name, "target_role": args.role, "files": files},
    )
    submission_id = payload.get("submission_id")
    if not isinstance(submission_id, str) or not submission_id:
        raise RuntimeError("backend did not return a submission id")
    print(f"Submitted skill {name} ({len(files)} file(s)) for role {args.role}.")
    print(f"Submission id: {submission_id}")
    print("An EzyHub admin must approve the submission before it appears in the role's skills.")


def _resolves_within(skill_dir: Path, rel: str) -> bool:
    """True only if skill_dir/rel resolves to a path still inside skill_dir.

    Defense-in-depth against a symlinked intermediate dir (e.g. an employee makes
    ``ezyhub-x/scripts`` a symlink to ``~/project``): realpath follows every symlink
    component, so a redirected write/delete target lands outside and is refused.
    """
    root = os.path.realpath(skill_dir)
    target = os.path.realpath(skill_dir / rel)
    return target == root or target.startswith(root + os.sep)


def write_bundle_files(skill_dir: Path, files: list[dict]) -> list[str]:
    """Write a server skill bundle under skill_dir; return the relative paths written.

    Unlike the backend's skillfs (which rmtree's the whole dir), this never deletes:
    files the employee added under skill_dir are left alone. Stale managed files are
    removed separately, driven by the manifest's managed_files tracking.
    """
    if len(files) > ROLE_BUNDLE_MAX_FILES:
        raise ValueError("too many files in bundle")
    total = 0
    resolved: list[tuple[str, bytes]] = []
    for f in files:
        path, data = decode_bundle_file(f)
        total += len(data)
        if total > ROLE_BUNDLE_MAX_BYTES:
            raise ValueError("bundle exceeds byte cap")
        if not _resolves_within(skill_dir, path):
            raise ValueError(f"refusing to write through symlink escaping skill dir: {path}")
        resolved.append((path, data))
    written: list[str] = []
    for path, data in resolved:
        target = skill_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        written.append(path)
    return written


def remove_managed_file(skill_dir: Path, rel: str) -> None:
    """Delete a previously manifest-tracked file, then prune emptied parent dirs."""
    try:
        validate_rel_path(rel)
    except ValueError:
        return
    # Never unlink through a symlinked intermediate dir that escapes skill_dir.
    if not _resolves_within(skill_dir, rel):
        return
    target = skill_dir / rel
    if target.is_file() or target.is_symlink():
        target.unlink()
    parent = target.parent
    while parent != skill_dir:
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent


def _skip_symlinked_skill_dir(skills_dir: Path, name: str) -> bool:
    """True (with a warning) if the top-level skill dir itself is a symlink.

    realpath-based checks root at the symlink target, so they cannot catch a
    symlinked top-level dir (e.g. ``skills/ezyhub-x -> ~/checkout``). Skip the
    skill entirely rather than write or delete through it.
    """
    if (skills_dir / name).is_symlink():
        print(
            f"warning: skills/{name} is a symlink; "
            "skipping to avoid touching files outside the skills dir"
        )
        return True
    return False


def cmd_sync_skills(args: argparse.Namespace) -> None:
    try:
        payload = request_json("GET", "/skills", backend_url=args.backend_url, token=read_codex_key())
    except RuntimeError as exc:
        if "HTTP 401" in str(exc):
            raise RuntimeError(
                f"{exc}\nHint: the EzyHub key in CODEX_HOME/.env was rejected. "
                f"Re-run enrollment ({HELPER_COMMAND} enroll-backend) or rotate the key "
                f"({HELPER_COMMAND} key-rotate)."
            ) from exc
        raise
    skills = payload.get("skills")
    if not isinstance(skills, list):
        raise RuntimeError("backend returned invalid skills payload")
    skills_dir = codex_home() / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)

    manifest = load_manifest(skills_dir)
    previous = set(manifest["managed"])
    previous_files: dict[str, list[str]] = manifest["managed_files"]
    current: set[str] = set()
    current_files: dict[str, list[str]] = {}
    for item in skills:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        files = item.get("files")
        if not isinstance(name, str) or not isinstance(files, list):
            continue
        if not name.startswith(COMPANY_SKILL_PREFIX):
            raise RuntimeError(f"refusing to write non-company skill name: {name}")
        if not COMPANY_SKILL_NAME_RE.fullmatch(name):
            raise RuntimeError(f"refusing to write unsafe skill name: {name}")
        skill_md = next((f for f in files if isinstance(f, dict) and f.get("path") == "SKILL.md"), None)
        if skill_md is None:
            raise RuntimeError(f"refusing to write skill bundle without SKILL.md: {name}")
        _, skill_md_data = decode_bundle_file(skill_md)
        if not skill_md_data.decode("utf-8", errors="replace").lstrip().startswith("---\n"):
            raise RuntimeError(f"refusing to write invalid skill without YAML frontmatter: {name}")
        if _skip_symlinked_skill_dir(skills_dir, name):
            continue
        written = write_bundle_files(skills_dir / name, files)
        for rel in sorted(set(previous_files.get(name, [])) - set(written)):
            remove_managed_file(skills_dir / name, rel)
        current.add(name)
        current_files[name] = sorted(set(written))

    for removed in sorted(previous - current):
        if not COMPANY_SKILL_NAME_RE.fullmatch(removed):
            continue
        if _skip_symlinked_skill_dir(skills_dir, removed):
            continue
        skill_dir = skills_dir / removed
        # Old-format manifests tracked no files; they only ever managed SKILL.md.
        for rel in sorted(set(previous_files.get(removed) or ["SKILL.md"])):
            remove_managed_file(skill_dir, rel)
        try:
            skill_dir.rmdir()
        except OSError:
            pass

    previous_mcp = set(manifest["mcp_servers"])
    servers = payload.get("mcp_servers")
    managed_mcp: list[str] = sorted(previous_mcp)
    if isinstance(servers, list):
        managed_mcp = apply_mcp_servers(codex_home(), servers, previous_mcp)
    write_manifest(skills_dir, sorted(current), managed_mcp, current_files)
    print(f"Synced {len(current)} EzyHub role skill(s) for role {payload.get('role')}.")
    print(f"Synced {len(managed_mcp)} EzyHub MCP server config(s).")
    print("Open a new Codex App thread for skill changes to appear.")


def cmd_configure_codex_app_kb_token(args: argparse.Namespace) -> None:
    token = read_codex_app_kb_token(args)
    launchctl_setenv(CODEX_APP_KB_TOKEN_ENV, token)
    print(f"Configured {CODEX_APP_KB_TOKEN_ENV} for future macOS GUI app launches.")
    print("Quit and reopen Codex App, then start a new thread for MCP auth to pick it up.")


def cmd_configure_codex_app_client_key(args: argparse.Namespace) -> None:
    key = read_codex_client_key(args)
    configure_codex_with_key(key, args.base_url, args.model)


def parse_codex_config_strings(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    current: dict[str, Any] = root
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        section_match = SECTION_RE.match(line)
        if section_match:
            current = root
            for part in section_match.group(1).split("."):
                value = current.setdefault(part, {})
                if not isinstance(value, dict):
                    value = {}
                    current[part] = value
                current = value
            continue
        value_match = STRING_VALUE_RE.match(line)
        if value_match:
            key, value = value_match.groups()
            # Enough TOML string unescaping for values written by our helper.
            current[key] = bytes(value, "utf-8").decode("unicode_escape")
            continue
        bool_match = BOOL_VALUE_RE.match(line)
        if bool_match:
            key, value = bool_match.groups()
            current[key] = value == "true"
    return root


def codex_app_config_status(*, expected_base_url: str, expected_model: str) -> dict[str, Any]:
    home = codex_home()
    config_path = home / "config.toml"
    auth_path = home / "auth.json"
    env_path = home / CODEX_ENV_FILE_NAME
    env_values = codex_env_values(home)
    config_payload: dict[str, Any] = {}
    config_text = ""
    config_error: str | None = None
    if config_path.exists():
        try:
            config_text = config_path.read_text(encoding="utf-8")
            config_payload = parse_codex_config_strings(config_text)
        except Exception as exc:
            config_error = str(exc)

    providers = config_payload.get("model_providers")
    providers = providers if isinstance(providers, dict) else {}
    model_provider = config_payload.get("model_provider")
    active_provider = providers.get(model_provider) if isinstance(model_provider, str) else None
    active_provider = active_provider if isinstance(active_provider, dict) else {}
    features = config_payload.get("features")
    features = features if isinstance(features, dict) else {}

    auth_configured = False
    chatgpt_login = False
    auth_error: str | None = None
    if auth_path.exists():
        try:
            auth_payload = json.loads(auth_path.read_text(encoding="utf-8"))
            auth_configured = bool(auth_payload.get("OPENAI_API_KEY"))
            account = auth_payload.get("account")
            chatgpt_login = bool(auth_payload.get("tokens")) or (
                isinstance(account, dict) and account.get("type") == "chatgpt"
            )
        except Exception as exc:
            auth_error = str(exc)

    base_url = active_provider.get("base_url")
    wire_api = active_provider.get("wire_api")
    inline_token_configured = bool(active_provider.get("experimental_bearer_token"))
    env_key_name = active_provider.get("env_key")
    env_key_name = env_key_name if isinstance(env_key_name, str) and env_key_name else None
    env_key_configured = bool(
        env_key_name
        and (os.environ.get(env_key_name, "").strip() or env_values.get(env_key_name, "").strip())
    )
    model = config_payload.get("model")
    expected_base_url = expected_base_url.rstrip("/")
    accepted_base_urls = {
        expected_base_url,
        DEFAULT_PUBLIC_GATEWAY_BASE_URL.rstrip("/"),
    }
    ezyhub_owned = ezyhub_owned_active_provider(config_text, model_provider)
    provider_configured = bool(active_provider)
    credential_configured = auth_configured or inline_token_configured or env_key_configured
    base_url_configured = isinstance(base_url, str) and base_url.rstrip("/") in accepted_base_urls
    requires_openai_auth = active_provider.get("requires_openai_auth") is True
    image_generation_off = features.get("image_generation") is False
    ready = bool(
        config_path.exists()
        and credential_configured
        and ezyhub_owned
        and provider_configured
        and base_url_configured
        and wire_api == "responses"
        and model == expected_model
        and not config_error
        and not auth_error
    )
    missing: list[str] = []
    if not config_path.exists():
        missing.append("config.toml")
    if not auth_path.exists() and not inline_token_configured and not env_key_configured:
        missing.append(f"{CODEX_ENV_FILE_NAME}:{env_key_name or CODEX_CLIENT_KEY_ENV} or auth.json")
    if not credential_configured:
        missing.append("provider.env_key/.env, provider.experimental_bearer_token, or auth.OPENAI_API_KEY")
    if not ezyhub_owned:
        missing.append(f"model_provider={EZYHUB_PROVIDER_NAME}")
    if not provider_configured:
        missing.append(f"model_providers.{model_provider or EZYHUB_PROVIDER_NAME}")
    if not base_url_configured:
        missing.append(f"model_providers.{model_provider or EZYHUB_PROVIDER_NAME}.base_url")
    if wire_api != "responses":
        missing.append(f"model_providers.{model_provider or EZYHUB_PROVIDER_NAME}.wire_api=responses")
    if model != expected_model:
        missing.append(f"model={expected_model}")

    return {
        "ready": ready,
        "codex_home": str(home),
        "config_path": str(config_path),
        "auth_path": str(auth_path),
        "expected": {
            "base_urls": sorted(accepted_base_urls),
            "model": expected_model,
            "provider_name": EZYHUB_PROVIDER_NAME,
            "wire_api": "responses",
        },
        "configured": {
            "config_exists": config_path.exists(),
            "env_exists": env_path.exists(),
            "provider_env_key": bool(env_key_name),
            "provider_env_key_value": env_key_configured,
            "auth_exists": auth_path.exists(),
            "auth_api_key": auth_configured,
            "provider_experimental_bearer_token": inline_token_configured,
            "credential": credential_configured,
            "model_provider_ezyhub_gateway": ezyhub_owned,
            "active_provider": provider_configured,
            "active_provider_base_url": base_url_configured,
            "active_provider_wire_api_responses": wire_api == "responses",
            "model": model == expected_model,
        },
        "observed": {
            "model_provider": model_provider if isinstance(model_provider, str) else None,
            "model": model if isinstance(model, str) else None,
            "active_provider_base_url": base_url if isinstance(base_url, str) else None,
            "active_provider_wire_api": wire_api if isinstance(wire_api, str) else None,
            "active_provider_env_key": env_key_name,
            "env_file_has_active_provider_key": bool(env_key_name and env_key_name in env_values),
            "active_provider_has_inline_token": inline_token_configured,
            "provider_id_retained": model_provider if ezyhub_owned and isinstance(model_provider, str) else None,
            "requires_openai_auth": requires_openai_auth,
            "inline_token_present": inline_token_configured,
            "chatgpt_login": chatgpt_login,
            "image_generation_off": image_generation_off,
        },
        "missing": sorted(set(missing)),
        "errors": {
            "config": config_error,
            "auth": auth_error,
        },
    }


def cmd_codex_app_config_status(args: argparse.Namespace) -> None:
    payload = codex_app_config_status(expected_base_url=args.base_url, expected_model=args.model)
    print(json.dumps(payload, indent=2, sort_keys=True))


def cmd_clear_codex_app_kb_token(args: argparse.Namespace) -> None:
    launchctl_unsetenv(CODEX_APP_KB_TOKEN_ENV)
    print(f"Cleared {CODEX_APP_KB_TOKEN_ENV} from the current macOS launchd user environment.")
    print("Quit and reopen Codex App for the cleared environment to take effect.")


def cmd_codex_app_kb_token_status(args: argparse.Namespace) -> None:
    configured = launchctl_getenv(CODEX_APP_KB_TOKEN_ENV) is not None
    print(json.dumps({"env_var": CODEX_APP_KB_TOKEN_ENV, "configured": configured}, indent=2, sort_keys=True))



def nested_missing(payload: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    configured = payload.get("configured")
    if isinstance(configured, dict):
        missing.extend(str(key) for key, value in configured.items() if not value)
    for group_name in ("cliproxy_management", "google_directory"):
        group = payload.get(group_name)
        if not isinstance(group, dict):
            continue
        group_missing = group.get("missing")
        if isinstance(group_missing, list):
            missing.extend(f"{group_name}.{item}" for item in group_missing if isinstance(item, str))
    return sorted(set(missing))


def check_gateway_key() -> dict[str, Any]:
    """Verify the gateway key in config.toml actually drives the model gateway.

    Reads the active provider's base_url + inline experimental_bearer_token (the
    exact credential Codex sends on model requests) and calls the gateway's
    `/models` endpoint with it. This is the check that matters for whether the
    employee can actually use Codex: a valid key returning the selected model
    means model requests will work. The key is never returned or logged.
    """
    home = codex_home()
    config_path = home / "config.toml"
    if not config_path.exists():
        return {"ok": False, "reason": "config.toml not found; run /enroll"}
    try:
        cfg = parse_codex_config_strings(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    providers = cfg.get("model_providers")
    providers = providers if isinstance(providers, dict) else {}
    model_provider = cfg.get("model_provider")
    active = providers.get(model_provider) if isinstance(model_provider, str) else None
    active = active if isinstance(active, dict) else {}
    base_url = active.get("base_url")
    token = active.get("experimental_bearer_token")
    model = cfg.get("model")
    if not isinstance(base_url, str) or not base_url.strip():
        return {"ok": False, "reason": "no gateway base_url in config.toml; run /enroll"}
    if not isinstance(token, str) or not token.strip():
        return {"ok": False, "reason": "no gateway key (experimental_bearer_token) in config.toml; run /enroll"}
    try:
        payload = request_json("GET", "/models", backend_url=base_url.strip().rstrip("/"), token=token.strip())
    except RuntimeError as exc:
        masked = str(exc).replace(token.strip(), "***")
        if "HTTP 401" in masked or "HTTP 403" in masked:
            return {"ok": False, "reason": "gateway rejected the key (invalid or revoked); run /key-rotate or /enroll"}
        return {"ok": False, "reason": "gateway request failed", "detail": masked}
    ids = [m.get("id") for m in payload.get("data", []) if isinstance(m, dict)]
    model_available = isinstance(model, str) and model in ids
    result: dict[str, Any] = {
        "ok": bool(ids) and (model_available or not isinstance(model, str)),
        "payload": {
            "gateway": base_url.strip().rstrip("/"),
            "model": model if isinstance(model, str) else None,
            "model_available": model_available,
            "model_count": len(ids),
        },
    }
    if isinstance(model, str) and not model_available:
        result["reason"] = f"key valid and gateway reachable, but model '{model}' is not served by the gateway"
    return result


def build_doctor_report(args: argparse.Namespace) -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {}

    # The check that actually determines whether the employee can use Codex:
    # does the config.toml key drive the model gateway.
    checks["gateway"] = check_gateway_key()

    try:
        payload = request_json("GET", "/health", backend_url=args.backend_url)
        checks["key_backend"] = {"ok": bool(payload.get("ok")), "payload": payload}
    except Exception as exc:
        checks["key_backend"] = {"ok": False, "error": str(exc)}

    admin_key_available = bool(args.admin_key or os.environ.get("EZYHUB_ADMIN_KEY"))
    if admin_key_available:
        try:
            payload = request_json(
                "GET",
                "/admin/config/status",
                backend_url=args.backend_url,
                extra_headers=admin_headers(args),
            )
            checks["control_plane_config"] = {
                "ok": bool(payload.get("production_ready")),
                "payload": payload,
            }
            ezyhub_app = payload.get("ezyhub_app")
            ezyhub_app = ezyhub_app if isinstance(ezyhub_app, dict) else {}
            checks["ezyhub_app"] = {
                "ok": bool(ezyhub_app.get("configured")),
                "payload": ezyhub_app,
            }
        except Exception as exc:
            checks["control_plane_config"] = {"ok": False, "error": str(exc)}
            checks["ezyhub_app"] = {"ok": False, "error": str(exc)}
        try:
            payload = request_json(
                "GET",
                "/admin/cliproxy/status",
                backend_url=args.backend_url,
                extra_headers=admin_headers(args),
            )
            checks["cliproxy_management"] = {
                "ok": bool(payload.get("configured") and payload.get("ok")),
                "payload": payload,
            }
        except Exception as exc:
            checks["cliproxy_management"] = {"ok": False, "error": str(exc)}
    else:
        checks["control_plane_config"] = {
            "ok": False,
            "skipped": True,
            "reason": "EZYHUB_ADMIN_KEY or --admin-key is required",
        }
        checks["ezyhub_app"] = {
            "ok": False,
            "skipped": True,
            "reason": "EZYHUB_ADMIN_KEY or --admin-key is required",
        }
        checks["cliproxy_management"] = {
            "ok": False,
            "skipped": True,
            "reason": "EZYHUB_ADMIN_KEY or --admin-key is required",
        }

    if args.no_kb:
        checks["kb_mcp"] = {"ok": False, "skipped": True, "reason": "--no-kb was set"}
    else:
        try:
            payload = request_json_url(args.kb_health_url)
            ready = bool(payload.get("ready"))
            configured = payload.get("configured")
            unprovisioned = (
                not ready
                and isinstance(configured, dict)
                and configured
                and not any(configured.values())
            )
            if unprovisioned:
                checks["kb_mcp"] = {
                    "ok": False,
                    "skipped": True,
                    "reason": "KB MCP is not provisioned yet; skipping until it is configured",
                    "payload": payload,
                }
            else:
                checks["kb_mcp"] = {"ok": ready, "payload": payload}
        except Exception as exc:
            checks["kb_mcp"] = {"ok": False, "error": str(exc)}

    return {
        "ready": all(item.get("ok") for item in checks.values() if not item.get("skipped")),
        "backend_url": args.backend_url,
        "kb_health_url": None if args.no_kb else args.kb_health_url,
        "checks": checks,
    }


def cmd_doctor(args: argparse.Namespace) -> None:
    report = build_doctor_report(args)
    auto_sync = auto_sync_status()

    if args.json:
        payload = dict(report)
        payload["auto_sync"] = auto_sync
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("EzyHub Codex doctor")
        print(f"Backend: {args.backend_url}")
        checks = report.get("checks", {})
        if not isinstance(checks, dict):
            checks = {}
        for name, item in checks.items():
            if not isinstance(item, dict):
                continue
            if item.get("skipped"):
                print(f"- {name}: skipped ({item.get('reason')})")
                continue
            if item.get("ok"):
                print(f"- {name}: ok")
                continue
            print(f"- {name}: not ready")
            if item.get("reason"):
                print(f"  {item['reason']}")
            if item.get("error"):
                print(f"  error: {item['error']}")
            payload = item.get("payload")
            if isinstance(payload, dict):
                missing = nested_missing(payload)
                if missing:
                    print(f"  missing: {', '.join(missing)}")
                if "base_url" in payload:
                    print(f"  base_url: {payload['base_url']}")
                if "public_base_url" in payload:
                    print(f"  public_base_url: {payload['public_base_url']}")
        auto_sync_state = "installed" if auto_sync.get("installed") else "not installed"
        print(f"- auto-sync: {auto_sync_state} ({auto_sync.get('platform')})")
        print(f"Overall: {'ready' if report['ready'] else 'not ready'}")

    if args.strict and not report["ready"]:
        raise RuntimeError("doctor checks are not ready")


def current_platform() -> str:
    return sys.platform


def build_cron_line(interval_hours: int, helper_path: str, backend_url: str) -> str:
    return (
        f"0 */{interval_hours} * * * {HELPER_PYTHON} {shlex.quote(helper_path)} "
        f"--backend-url {shlex.quote(backend_url)} sync-skills >/dev/null 2>&1 # {AUTO_SYNC_MARKER}"
    )


def merge_crontab_text(existing: str, new_line: str | None) -> str:
    lines = [line for line in existing.splitlines() if AUTO_SYNC_MARKER not in line]
    if new_line is not None:
        lines.append(new_line)
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def build_launchd_plist(interval_hours: int, helper_path: str, backend_url: str) -> str:
    seconds = interval_hours * 3600
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LAUNCHD_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>python3</string>
        <string>{helper_path}</string>
        <string>--backend-url</string>
        <string>{backend_url}</string>
        <string>sync-skills</string>
    </array>
    <key>StartInterval</key>
    <integer>{seconds}</integer>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
"""


def build_schtasks_create_args(interval_hours: int, helper_path: str, backend_url: str) -> list[str]:
    command = f'"{HELPER_PYTHON}" "{helper_path}" --backend-url "{backend_url}" sync-skills'
    return [
        "schtasks",
        "/Create",
        "/TN",
        SCHTASKS_TASK_NAME,
        "/TR",
        command,
        "/SC",
        "HOURLY",
        "/MO",
        str(interval_hours),
        "/F",
    ]


def launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def _read_crontab() -> str:
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if result.returncode != 0:
        return ""
    return result.stdout


def _write_crontab(text: str) -> None:
    subprocess.run(["crontab", "-"], input=text, text=True, check=True)


def _install_auto_sync_linux(interval_hours: int, helper_path: str, backend_url: str) -> str:
    line = build_cron_line(interval_hours, helper_path, backend_url)
    _write_crontab(merge_crontab_text(_read_crontab(), line))
    return "Installed cron job for automatic skill sync."


def _uninstall_auto_sync_linux() -> str:
    _write_crontab(merge_crontab_text(_read_crontab(), None))
    return "Removed cron job for automatic skill sync."


def _install_auto_sync_darwin(interval_hours: int, helper_path: str, backend_url: str) -> str:
    plist_path = launchd_plist_path()
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(build_launchd_plist(interval_hours, helper_path, backend_url), encoding="utf-8")
    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True, text=True)
    subprocess.run(["launchctl", "load", str(plist_path)], capture_output=True, text=True, check=True)
    return "Installed launchd job for automatic skill sync."


def _uninstall_auto_sync_darwin() -> str:
    plist_path = launchd_plist_path()
    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True, text=True)
    if plist_path.exists():
        plist_path.unlink()
    return "Removed launchd job for automatic skill sync."


def _install_auto_sync_win32(interval_hours: int, helper_path: str, backend_url: str) -> str:
    args = build_schtasks_create_args(interval_hours, helper_path, backend_url)
    subprocess.run(args, capture_output=True, text=True, check=True)
    return "Installed scheduled task for automatic skill sync."


def _uninstall_auto_sync_win32() -> str:
    result = subprocess.run(
        ["schtasks", "/Delete", "/TN", SCHTASKS_TASK_NAME, "/F"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return "Auto-sync task was not installed."
    return "Removed scheduled task for automatic skill sync."


def install_auto_sync(interval_hours: int, backend_url: str) -> str:
    if not (1 <= interval_hours <= 23):
        raise RuntimeError(f"auto-sync interval hours must be between 1 and 23, got {interval_hours}")
    helper_path = str(Path(__file__).resolve())
    platform = current_platform()
    if platform == "darwin":
        return _install_auto_sync_darwin(interval_hours, helper_path, backend_url)
    if platform == "win32":
        return _install_auto_sync_win32(interval_hours, helper_path, backend_url)
    return _install_auto_sync_linux(interval_hours, helper_path, backend_url)


def uninstall_auto_sync() -> str:
    platform = current_platform()
    if platform == "darwin":
        return _uninstall_auto_sync_darwin()
    if platform == "win32":
        return _uninstall_auto_sync_win32()
    return _uninstall_auto_sync_linux()


def auto_sync_status() -> dict[str, Any]:
    platform = current_platform()
    try:
        if platform == "darwin":
            installed = launchd_plist_path().exists()
            detail = str(launchd_plist_path())
        elif platform == "win32":
            result = subprocess.run(
                ["schtasks", "/Query", "/TN", SCHTASKS_TASK_NAME],
                capture_output=True,
                text=True,
            )
            installed = result.returncode == 0
            detail = result.stdout.strip() or result.stderr.strip()
        else:
            crontab_text = _read_crontab()
            installed = AUTO_SYNC_MARKER in crontab_text
            detail = "crontab entry found" if installed else "no crontab entry"
    except Exception as exc:
        return {"installed": False, "platform": platform, "detail": str(exc)}
    return {"installed": installed, "platform": platform, "detail": detail}


def cmd_install_auto_sync(args: argparse.Namespace) -> None:
    summary = install_auto_sync(args.interval_hours, args.backend_url)
    print(f"{summary} (every {args.interval_hours}h)")


def cmd_uninstall_auto_sync(args: argparse.Namespace) -> None:
    print(uninstall_auto_sync())


def cmd_key_rotate(args: argparse.Namespace) -> None:
    payload = request_json(
        "POST",
        "/keys/rotate",
        backend_url=args.backend_url,
        token=read_codex_key(),
    )
    key = payload.get("key")
    if not isinstance(key, str) or not key:
        raise RuntimeError("backend did not return a rotated key")
    configure_codex_with_key(key, args.base_url, args.model)


def cmd_enroll_backend(args: argparse.Namespace) -> None:
    created = request_json("POST", "/enroll/sessions", backend_url=args.backend_url)
    # flush=True: agents often run this in a non-interactive shell where stdout is
    # block-buffered; without flushing, the authorization URL stays invisible until
    # the helper exits — exactly when it is no longer useful.
    print("A browser window is opening for EzyHub authorization.", flush=True)
    print("If you are already signed in to EzyHub with your company Google account, just click \"Authorize Codex\".", flush=True)
    print(f"AUTHORIZATION LINK (safe to share with the user): {created['browser_url']}", flush=True)
    print("If no window opened — or the browser that opened is not signed in to EzyHub — open this link in a browser profile signed in with the company Google account.", flush=True)
    if not args.no_open_browser:
        webbrowser.open(created["browser_url"])
    print(f"Waiting for authorization (up to {args.poll_timeout_seconds // 60} minutes)...", flush=True)
    if args.dev_complete:
        body: dict[str, Any] = {
            "session_id": created["session_id"],
            "google_sub": args.dev_google_sub,
            "google_email": args.dev_google_email,
            "name": args.dev_name,
            "role": args.dev_role,
        }
        request_json(
            "POST",
            "/dev/enroll/complete",
            backend_url=args.backend_url,
            body=body,
        )
    deadline = time.monotonic() + args.poll_timeout_seconds
    result: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        result = request_json(
            "GET",
            f"/enroll/sessions/{created['session_id']}/result",
            backend_url=args.backend_url,
            query={"device_secret": created["device_secret"]},
        )
        if result.get("status") != "pending":
            break
        time.sleep(args.poll_interval_seconds)
    if result is None:
        raise RuntimeError("enroll result was not returned")
    if result.get("status") != "complete":
        raise RuntimeError(f"enroll result not complete yet: {result.get('status')}")
    key = result.get("key")
    if not isinstance(key, str) or not key:
        raise RuntimeError("enroll result did not include a key")
    configure_codex_with_key(key, args.base_url, args.model)
    # read_codex_key reads the freshly written CODEX_HOME/.env directly (not the
    # process env), so the in-process sync below and future runs use this new key.
    print("Codex provider and key configured.")
    if not args.skip_sync_skills:
        try:
            cmd_sync_skills(args)
        except Exception:
            print(
                "Enrollment step failed: skill/MCP sync. Key is already configured. "
                f"Resume with: {HELPER_COMMAND} sync-skills, then: {HELPER_COMMAND} install-auto-sync"
            )
            raise
    if not getattr(args, "skip_auto_sync", False):
        try:
            summary = install_auto_sync(args.auto_sync_interval_hours, args.backend_url)
            print(summary)
        except Exception:
            print(f"Enrollment step failed: auto-sync install. Resume with: {HELPER_COMMAND} install-auto-sync")
            raise
    role = result.get("role")
    print(f"Enrolled{f' with role {role}' if role else ''}. Quit and reopen Codex App to pick up the new configuration.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="EzyHub Codex backend helper")
    parser.add_argument("--backend-url", default=os.environ.get("EZYHUB_CODEX_BACKEND_URL", DEFAULT_BACKEND_URL))
    parser.add_argument("--admin-key", default=os.environ.get("EZYHUB_ADMIN_KEY"))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status")
    sub.add_parser("sync-skills")
    publish = sub.add_parser("publish-skill")
    publish.add_argument("dir")
    publish.add_argument("--role", required=True)
    publish.add_argument("--name")
    configure_app_kb_token = sub.add_parser("configure-codex-app-kb-token")
    configure_app_kb_token.add_argument("--token-file")
    configure_app_kb_token.add_argument("--prompt-token", action="store_true")
    configure_client_key = sub.add_parser("configure-codex-app-client-key")
    configure_client_key.add_argument("--key-file")
    configure_client_key.add_argument("--prompt-key", action="store_true")
    configure_client_key.add_argument("--base-url", default=DEFAULT_GATEWAY_BASE_URL)
    configure_client_key.add_argument("--model", default="gpt-5.6-sol")
    sub.add_parser("clear-codex-app-kb-token")
    sub.add_parser("codex-app-kb-token-status")

    doctor = sub.add_parser("doctor")
    doctor.add_argument("--kb-health-url", default=os.environ.get("EZYHUB_KB_MCP_HEALTH_URL", DEFAULT_KB_HEALTH_URL))
    doctor.add_argument("--no-kb", action="store_true")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--strict", action="store_true")

    app_config = sub.add_parser("codex-app-config-status")
    app_config.add_argument("--base-url", default=DEFAULT_GATEWAY_BASE_URL)
    app_config.add_argument("--model", default="gpt-5.6-sol")

    rotate = sub.add_parser("key-rotate")
    rotate.add_argument("--base-url", default=DEFAULT_GATEWAY_BASE_URL)
    rotate.add_argument("--model", default="gpt-5.6-sol")

    enroll = sub.add_parser("enroll-backend")
    enroll.add_argument("--base-url", default=DEFAULT_GATEWAY_BASE_URL)
    enroll.add_argument("--model", default="gpt-5.6-sol")
    enroll.add_argument("--dev-complete", action="store_true")
    enroll.add_argument("--dev-google-sub", default="dev-user")
    enroll.add_argument("--dev-google-email", default="dev@example.com")
    enroll.add_argument("--dev-name", default="Dev User")
    enroll.add_argument("--dev-role", default="engineering")
    enroll.add_argument("--no-open-browser", action="store_true")
    enroll.add_argument("--skip-sync-skills", action="store_true")
    enroll.add_argument("--skip-auto-sync", action="store_true")
    enroll.add_argument("--auto-sync-interval-hours", type=int, default=4)
    enroll.add_argument("--poll-timeout-seconds", type=int, default=600)
    enroll.add_argument("--poll-interval-seconds", type=float, default=3.0)

    install_auto_sync_parser = sub.add_parser("install-auto-sync")
    install_auto_sync_parser.add_argument("--interval-hours", type=int, default=4)
    sub.add_parser("uninstall-auto-sync")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "status":
            cmd_status(args)
        elif args.command == "sync-skills":
            cmd_sync_skills(args)
        elif args.command == "publish-skill":
            cmd_publish_skill(args)
        elif args.command == "configure-codex-app-kb-token":
            cmd_configure_codex_app_kb_token(args)
        elif args.command == "configure-codex-app-client-key":
            cmd_configure_codex_app_client_key(args)
        elif args.command == "clear-codex-app-kb-token":
            cmd_clear_codex_app_kb_token(args)
        elif args.command == "codex-app-kb-token-status":
            cmd_codex_app_kb_token_status(args)
        elif args.command == "codex-app-config-status":
            cmd_codex_app_config_status(args)
        elif args.command == "doctor":
            cmd_doctor(args)
        elif args.command == "key-rotate":
            cmd_key_rotate(args)
        elif args.command == "enroll-backend":
            cmd_enroll_backend(args)
        elif args.command == "install-auto-sync":
            cmd_install_auto_sync(args)
        elif args.command == "uninstall-auto-sync":
            cmd_uninstall_auto_sync(args)
        else:
            raise RuntimeError(f"unknown command {args.command}")
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
