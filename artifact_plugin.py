"""Portable standard-library client for an Artifact Relay service."""

from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from contextlib import suppress
from ipaddress import ip_address
from pathlib import Path
from typing import Any

TOKEN_ENV = "ARTIFACT_RELAY_API_TOKEN"
ARTIFACT_ID = re.compile(r"[A-Za-z0-9_-]{22,64}")
MAX_CONTENT_BYTES = 5 * 1024 * 1024
PROVENANCE_FIELDS = (
    "session_id",
    "session_title",
    "platform",
    "chat_name",
    "topic_id",
    "topic_name",
)


class ArtifactError(RuntimeError):
    """A safe, model-facing publisher failure."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Fail closed so a redirect can never receive the bearer credential."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def environment_token() -> str:
    """Read the sole required secret from the cross-platform environment."""
    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        raise ArtifactError(
            "Artifact Relay is unavailable. Set ARTIFACT_RELAY_API_TOKEN "
            "in the Hermes secret environment, then restart Hermes."
        )
    return token


def requirements_available() -> bool:
    """Return whether the required bearer token is configured."""
    return bool(os.environ.get(TOKEN_ENV, "").strip())


def session_metadata(
    session_id: str | None,
    *,
    state_db: Path | None = None,
) -> dict[str, str]:
    """Best-effort provenance; publishing never depends on Hermes internals."""
    if not session_id:
        return {}
    fallback = {"session_id": session_id}
    if state_db is None:
        try:
            from hermes_constants import get_hermes_home

            state_db = get_hermes_home() / "state.db"
        except (ImportError, OSError):
            home = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
            state_db = home / "state.db"
    try:
        with sqlite3.connect(f"file:{state_db}?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT title, source, thread_id, display_name, origin_json "
                "FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
    except (OSError, sqlite3.Error):
        return fallback
    if row is None:
        return fallback
    try:
        origin = json.loads(row["origin_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        origin = {}
    if not isinstance(origin, dict):
        origin = {}
    result = {
        "session_id": session_id,
        "session_title": str(row["title"] or "").strip(),
        "platform": str(row["source"] or origin.get("platform") or "").strip(),
        "chat_name": str(row["display_name"] or origin.get("chat_name") or "").strip(),
        "topic_id": str(row["thread_id"] or origin.get("thread_id") or "").strip(),
        "topic_name": str(origin.get("topic_name") or origin.get("chat_topic") or "").strip(),
    }
    return {key: value[:512] for key, value in result.items() if value}


def _disposition(name: str, filename: str | None = None) -> bytes:
    value = f'form-data; name="{name}"'
    if filename is not None:
        value += f'; filename="{filename}"'
    return value.encode("ascii")


def _multipart(fields: list[tuple[str, str]], content: bytes, filename: str) -> tuple[bytes, str]:
    boundary = "----hermes-artifact-" + secrets.token_hex(18)
    marker = boundary.encode("ascii")
    chunks: list[bytes] = []
    for name, value in fields:
        chunks.extend(
            (
                b"--" + marker + b"\r\n",
                b"Content-Disposition: " + _disposition(name) + b"\r\n\r\n",
                value.encode(),
                b"\r\n",
            )
        )
    media_type = mimetypes.guess_type(filename)[0] or "text/plain"
    chunks.extend(
        (
            b"--" + marker + b"\r\n",
            b"Content-Disposition: " + _disposition("content", filename) + b"\r\n",
            f"Content-Type: {media_type}\r\n\r\n".encode("ascii"),
            content,
            b"\r\n",
            b"--" + marker + b"--\r\n",
        )
    )
    return b"".join(chunks), boundary


class ArtifactClient:
    """Authenticated client constrained to one configured publisher origin."""

    def __init__(
        self,
        base_url: str,
        *,
        token_provider: Callable[[], str] = environment_token,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._base = urllib.parse.urlsplit(self.base_url)
        try:
            port = self._base.port
        except ValueError as exc:
            raise ValueError("publisher base URL must be a canonical HTTP(S) origin") from exc
        if (
            self._base.scheme not in {"http", "https"}
            or not self._base.hostname
            or self._base.username is not None
            or self._base.password is not None
            or self._base.path
            or self._base.query
            or self._base.fragment
        ):
            raise ValueError("publisher base URL must be a canonical HTTP(S) origin")
        _ = port
        hostname = self._base.hostname
        is_loopback = hostname.lower() == "localhost"
        if not is_loopback:
            with suppress(ValueError):
                is_loopback = ip_address(hostname).is_loopback
        if self._base.scheme == "http" and not is_loopback:
            raise ValueError("publisher base URL requires HTTPS except on loopback")
        self._token_provider = token_provider
        self._opener = urllib.request.build_opener(_NoRedirect())

    def _artifact_id(self, value: str) -> str:
        if ARTIFACT_ID.fullmatch(value):
            return value
        candidate = urllib.parse.urlsplit(value)
        if (
            candidate.scheme != self._base.scheme
            or candidate.netloc != self._base.netloc
            or candidate.query
            or candidate.fragment
        ):
            raise ValueError("artifact URL must use the configured publisher origin")
        match = re.fullmatch(r"/a/([A-Za-z0-9_-]{22,64})/?", candidate.path)
        if not match:
            raise ValueError("expected an artifact URL shaped like /a/<id>")
        return match.group(1)

    def _request(self, request: urllib.request.Request) -> dict[str, Any]:
        request.add_header("Authorization", f"Bearer {self._token_provider()}")
        request.add_header("Accept", "application/json")
        request.add_header("User-Agent", "Hermes-Artifact-Relay/1.0")
        try:
            with self._opener.open(request, timeout=90) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            raise ArtifactError(f"Artifact Relay returned HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise ArtifactError("Artifact Relay is unreachable") from exc
        if not isinstance(result, dict):
            raise ArtifactError("Artifact Relay returned an unexpected response")
        return result

    def read(self, url_or_id: str) -> dict[str, Any]:
        identifier = self._artifact_id(url_or_id)
        result = self._request(
            urllib.request.Request(f"{self.base_url}/api/artifacts/{identifier}")
        )
        if not isinstance(result.get("content"), str):
            raise ArtifactError("Artifact Relay response has no source content")
        return result

    def publish(
        self,
        *,
        title: str,
        content: str,
        summary: str = "",
        fmt: str = "markdown",
        expires_days: int = 30,
        provenance: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        if fmt not in {"markdown", "html"}:
            raise ValueError("format must be markdown or html")
        payload = content.encode()
        if not payload or len(payload) > MAX_CONTENT_BYTES:
            raise ValueError("content must be between 1 byte and 5 MiB")
        if not 0 <= expires_days <= 3650:
            raise ValueError("expires_days must be between 0 and 3650")
        fields = [
            ("title", title),
            ("summary", summary),
            ("format", fmt),
            ("expires_in_days", str(expires_days)),
        ]
        for name in PROVENANCE_FIELDS:
            value = (provenance or {}).get(name)
            if value:
                fields.append((name, value[:512]))
        filename = "artifact.md" if fmt == "markdown" else "artifact.html"
        body, boundary = _multipart(fields, payload, filename)
        result = self._request(
            urllib.request.Request(
                f"{self.base_url}/api/artifacts",
                data=body,
                method="POST",
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            )
        )
        viewer_url = result.get("url")
        if not isinstance(viewer_url, str):
            raise ArtifactError("Artifact Relay returned an unexpected artifact URL")
        try:
            self._artifact_id(viewer_url)
        except ValueError as exc:
            raise ArtifactError("Artifact Relay returned an unexpected artifact URL") from exc
        return result
