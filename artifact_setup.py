"""Operator-only hosted setup for Hermes Artifact Relay."""

from __future__ import annotations

import importlib
import inspect
import json
import math
import os
import re
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

try:
    from .artifact_plugin import TOKEN_ENV, ArtifactClient, ArtifactError, _NoRedirect
except ImportError:  # pragma: no cover - direct source loading in repository tests
    from artifact_plugin import TOKEN_ENV, ArtifactClient, ArtifactError, _NoRedirect

DEFAULT_CONTROL_PLANE = "https://relay.lok-labs.com"
CONFIG_KEY = "plugins.entries.artifact-relay.settings.base_url"


class SetupError(RuntimeError):
    """A credential-safe setup failure."""


def _origin(value: str, label: str) -> str:
    try:
        return ArtifactClient(value, token_provider=lambda: "unused").base_url
    except ValueError as exc:
        raise SetupError(f"{label} must be an HTTPS origin (HTTP is loopback-only)") from exc


def _post(
    opener: Any, url: str, payload: dict[str, str], *, timeout: float
) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Hermes-Artifact-Relay-Setup/1.0",
        },
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            status = response.status
            value = json.load(response)
    except urllib.error.HTTPError as exc:
        status = exc.code
        if 300 <= status < 400:
            raise SetupError("Artifact Relay setup refused an HTTP redirect") from exc
        try:
            value = json.loads(exc.read(64 * 1024))
        except (ValueError, OSError) as parse_exc:
            raise SetupError(f"Artifact Relay setup returned HTTP {status}") from parse_exc
    except TimeoutError as exc:
        raise SetupError("Artifact Relay authorization timed out") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise SetupError("Artifact Relay authorization timed out") from exc
        raise SetupError("Artifact Relay setup service is unreachable") from exc
    except OSError as exc:
        raise SetupError("Artifact Relay setup service is unreachable") from exc
    if not isinstance(value, dict):
        raise SetupError("Artifact Relay setup returned an unexpected response")
    return status, value


def _token_assignments(value: str) -> list[str]:
    pattern = re.compile(rf"^\s*(?:export\s+)?{re.escape(TOKEN_ENV)}\s*=\s*(.*)$")
    assignments: list[str] = []
    for line in value.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        serialized = match.group(1).strip()
        if len(serialized) >= 2 and serialized[0] == serialized[-1] == '"':
            with suppress(json.JSONDecodeError):
                serialized = json.loads(serialized)
        elif len(serialized) >= 2 and serialized[0] == serialized[-1] == "'":
            serialized = serialized[1:-1]
        assignments.append(serialized)
    return assignments


def _portable_save_token(token: str, hermes_home: Path) -> None:
    try:
        hermes_home.mkdir(parents=True, exist_ok=True)
        env_path = hermes_home / ".env"
        existing_bytes = env_path.read_bytes() if env_path.exists() else b""
        has_bom = existing_bytes.startswith(b"\xef\xbb\xbf")
        existing = existing_bytes.decode("utf-8-sig")
        original_mode = stat.S_IMODE(env_path.stat().st_mode) if env_path.exists() else None
        lines = existing.splitlines(keepends=True)
        prefix = f"{TOKEN_ENV}="
        replacement = f"{prefix}{token}\n"
        output: list[str] = []
        replaced = False
        for line in lines:
            stripped = line.strip()
            defines_token = stripped.startswith(prefix) or stripped.startswith(f"export {prefix}")
            if defines_token:
                if not replaced:
                    output.append(replacement)
                    replaced = True
            else:
                output.append(line)
        if output and not output[-1].endswith(("\n", "\r")):
            output[-1] += "\n"
        if not replaced:
            output.append(replacement)
        fd, temporary = tempfile.mkstemp(prefix=".env.", dir=hermes_home, text=True)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
                if has_bom:
                    stream.write("\ufeff")
                stream.writelines(output)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, env_path)
            os.chmod(env_path, original_mode if original_mode is not None else 0o600)
        except BaseException:
            with suppress(FileNotFoundError):
                os.unlink(temporary)
            raise
    except (OSError, UnicodeError) as exc:
        raise SetupError("Could not save the Artifact Relay credential") from exc


def _verified_assignments(hermes_home: Path) -> list[str]:
    try:
        return _token_assignments((hermes_home / ".env").read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError) as exc:
        raise SetupError("Could not save the Artifact Relay credential") from exc


def _save_token(token: str, hermes_home: Path) -> None:
    if not token or any(character in token for character in "\r\n\x00"):
        raise SetupError("Artifact Relay setup returned an invalid credential")
    if hermes_home.expanduser().absolute() == _hermes_home().expanduser().absolute():
        try:
            config_module = importlib.import_module("hermes_cli.config")
        except ImportError:
            pass
        else:
            try:
                hermes_module = importlib.import_module("hermes_cli")
                managed_scope = getattr(hermes_module, "managed_scope", None)
                if managed_scope is None:
                    with suppress(ImportError):
                        managed_scope = importlib.import_module("hermes_cli.managed_scope")
                is_env_managed = getattr(managed_scope, "is_env_managed", None)
                is_managed = getattr(config_module, "is_managed", None)
                if (callable(is_managed) and is_managed()) or (
                    callable(is_env_managed) and is_env_managed(TOKEN_ENV)
                ):
                    raise SetupError("Could not save the Artifact Relay credential")
                config_module.save_env_value(TOKEN_ENV, token)
                assignments = _verified_assignments(hermes_home)
                if len(assignments) > 1 or (assignments and assignments[0] != token):
                    _portable_save_token(token, hermes_home)
                    assignments = _verified_assignments(hermes_home)
                if assignments != [token]:
                    raise SetupError("Could not save the Artifact Relay credential")
            except SetupError:
                raise
            except Exception as exc:
                raise SetupError("Could not save the Artifact Relay credential") from exc
            return
    _portable_save_token(token, hermes_home)


def save_base_url(base_url: str, *, timeout: float | None = None) -> None:
    """Persist the non-secret origin through Hermes' supported config CLI."""
    try:
        subprocess.run(
            ["hermes", "config", "set", CONFIG_KEY, base_url, "--force"],
            check=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SetupError("Could not save the Artifact Relay service URL") from exc


def _hermes_home() -> Path:
    value = os.environ.get("HERMES_HOME")
    return Path(value).expanduser() if value else Path.home() / ".hermes"


def _verification_url(value: str, *, origin: str, user_code: str) -> str:
    expected = f"{origin}/activate/{urllib.parse.quote(user_code, safe='')}"
    if value != expected:
        raise SetupError("Artifact Relay setup returned an invalid verification URL")
    return value


def _publish_smoke(base_url: str, token: str, *, timeout: float = 90) -> str:
    """Prove the newly issued tenant credential with a real publication."""
    client = ArtifactClient(base_url, token_provider=lambda: token)
    try:
        result = client.publish(
            title="Artifact Relay connected",
            content=(
                "# Artifact Relay connected\n\n"
                "Hermes completed the managed setup and verified this publication.\n"
            ),
            summary="Managed setup verification",
            timeout=timeout,
        )
    except ArtifactError as exc:
        raise SetupError(
            "Artifact Relay credentials were saved, but the test publication failed"
        ) from exc
    url = result.get("url")
    if not isinstance(url, str) or not url:
        raise SetupError("Artifact Relay test publication returned an unexpected response")
    return url


def hosted_setup(
    *,
    control_plane: str = DEFAULT_CONTROL_PLANE,
    timeout: float = 600,
    hermes_home: Path | None = None,
    output: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    config_setter: Callable[..., None] = save_base_url,
    smoke_publisher: Callable[..., str] = _publish_smoke,
) -> None:
    """Complete device authorization and persist the result without returning secrets."""
    origin = _origin(control_plane, "control plane")
    if not math.isfinite(timeout) or timeout <= 0:
        raise SetupError("timeout must be finite and greater than zero")
    deadline = monotonic() + timeout

    def remaining() -> float:
        value = deadline - monotonic()
        if value <= 0:
            raise SetupError("Artifact Relay authorization timed out")
        return value

    def request_timeout() -> float:
        return min(30.0, remaining())

    def timed_call(callback: Callable[..., Any], *args: str) -> Any:
        operation_timeout = remaining()
        try:
            inspect.signature(callback).bind(*args, timeout=operation_timeout)
        except (TypeError, ValueError):
            return callback(*args)
        return callback(*args, timeout=operation_timeout)

    opener = urllib.request.build_opener(_NoRedirect())
    status, authorization = _post(
        opener,
        f"{origin}/api/device/authorizations",
        {},
        timeout=request_timeout(),
    )
    remaining()
    if status >= 400:
        raise SetupError(f"Artifact Relay setup returned HTTP {status}")
    try:
        device_code = str(authorization["device_code"])
        user_code = str(authorization["user_code"])
        verification_uri = str(authorization["verification_uri_complete"])
        interval = float(authorization.get("interval", 5))
        expires_in = float(authorization["expires_in"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SetupError("Artifact Relay setup returned an unexpected response") from exc
    if (
        not device_code
        or not user_code
        or not verification_uri
        or not math.isfinite(interval)
        or not math.isfinite(expires_in)
        or expires_in <= 0
    ):
        raise SetupError("Artifact Relay setup returned an unexpected response")
    interval = max(1.0, interval)
    verification_uri = _verification_url(
        verification_uri,
        origin=origin,
        user_code=user_code,
    )
    output(f"Open: {verification_uri}")
    output(f"Code: {user_code}")
    authorization_deadline = min(deadline, monotonic() + expires_in)
    while True:
        if monotonic() + interval >= authorization_deadline:
            raise SetupError("Artifact Relay authorization timed out")
        sleep(interval)
        status, token_response = _post(
            opener,
            f"{origin}/api/device/token",
            {"device_code": device_code},
            timeout=request_timeout(),
        )
        remaining()
        error = token_response.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        if error in {"expired_token", "authorization_expired"}:
            raise SetupError("Artifact Relay authorization expired")
        if status >= 400 or error:
            raise SetupError("Artifact Relay authorization failed")
        api_token = token_response.get("api_token")
        base_url = token_response.get("base_url")
        if not isinstance(api_token, str) or not isinstance(base_url, str):
            raise SetupError("Artifact Relay setup returned an unexpected response")
        validated_base_url = _origin(base_url, "service URL")
        _save_token(api_token, hermes_home or _hermes_home())
        remaining()
        timed_call(config_setter, validated_base_url)
        remaining()
        smoke_url = timed_call(smoke_publisher, validated_base_url, api_token)
        remaining()
        output(f"Test artifact: {smoke_url}")
        output("Artifact Relay connected. Start a new Hermes session to activate its tools.")
        return None
