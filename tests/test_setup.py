from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from artifact_setup import SetupError, _save_token, hosted_setup, save_base_url


class DeviceFlow(BaseHTTPRequestHandler):
    token_requests = 0
    bodies: list[dict[str, str]] = []
    api_token = "relay-secret-token"

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
        type(self).bodies.append(body)
        origin = f"http://127.0.0.1:{self.server.server_port}"
        if self.path == "/api/device/authorizations":
            self._json(
                {
                    "device_code": "one-time-device-code",
                    "user_code": "ABCD-EFGH",
                    "verification_uri_complete": f"{origin}/activate/ABCD-EFGH",
                    "interval": 1,
                    "expires_in": 30,
                }
            )
            return
        if self.path == "/api/device/token":
            type(self).token_requests += 1
            if self.token_requests == 1:
                self._json({"error": "authorization_pending"}, 400)
            elif self.token_requests == 2:
                self._json({"error": "slow_down"}, 400)
            else:
                self._json({"base_url": origin, "api_token": self.api_token})
            return
        self.send_error(404)

    def _json(self, value: dict[str, object], status: int = 200) -> None:
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        pass


def test_secret_writer_is_windows_compatible_and_replaces_export_assignment(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("export ARTIFACT_RELAY_API_TOKEN=old-secret\nOTHER=keep\n")
    monkeypatch.delattr(os, "fchmod", raising=False)

    _save_token("new-secret", tmp_path)

    value = env_path.read_text()
    assert "old-secret" not in value
    assert value.count("ARTIFACT_RELAY_API_TOKEN=") == 1
    assert "ARTIFACT_RELAY_API_TOKEN=new-secret" in value
    assert "OTHER=keep" in value


def test_secret_writer_wraps_portable_storage_failures(tmp_path):
    invalid_home = tmp_path / "not-a-directory"
    invalid_home.write_text("occupied")

    with pytest.raises(SetupError, match="Could not save"):
        _save_token("new-secret", invalid_home)


def test_secret_writer_preserves_existing_bom_and_permissions(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_bytes(
        b"\xef\xbb\xbfexport ARTIFACT_RELAY_API_TOKEN=old-secret\r\nOTHER=keep\r\n"
    )
    env_path.chmod(0o640)

    _save_token("new-secret", tmp_path)

    value = env_path.read_bytes()
    assert value.startswith(b"\xef\xbb\xbf")
    assert b"old-secret" not in value
    assert value.count(b"ARTIFACT_RELAY_API_TOKEN=") == 1
    if os.name != "nt":
        assert stat.S_IMODE(env_path.stat().st_mode) == 0o640


def test_secret_writer_verifies_supported_hermes_writer_for_active_profile(tmp_path, monkeypatch):
    calls: list[tuple[str, str]] = []
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setitem(sys.modules, "hermes_cli", SimpleNamespace())

    def save_env_value(key: str, value: str) -> None:
        calls.append((key, value))
        (tmp_path / ".env").write_text(f"{key}={value}\n")

    monkeypatch.setitem(
        sys.modules,
        "hermes_cli.config",
        SimpleNamespace(save_env_value=save_env_value),
    )

    _save_token("new-secret", tmp_path)

    assert calls == [("ARTIFACT_RELAY_API_TOKEN", "new-secret")]
    assert (tmp_path / ".env").read_text() == "ARTIFACT_RELAY_API_TOKEN=new-secret\n"


def test_secret_writer_cleans_duplicates_left_by_real_host_writer_semantics(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "export ARTIFACT_RELAY_API_TOKEN=old-first\nOTHER=keep\nARTIFACT_RELAY_API_TOKEN=old-last\n"
    )
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setitem(sys.modules, "hermes_cli", SimpleNamespace())

    def save_like_host_writer(key: str, value: str) -> None:
        lines = env_path.read_text().splitlines(keepends=True)
        for index, line in enumerate(lines):
            if line.startswith(f"{key}=") or line.startswith(f"export {key}="):
                lines[index] = f"{key}={value}\n"
                break
        env_path.write_text("".join(lines))

    monkeypatch.setitem(
        sys.modules,
        "hermes_cli.config",
        SimpleNamespace(save_env_value=save_like_host_writer),
    )

    _save_token("new-secret", tmp_path)

    value = env_path.read_text()
    assert value == "ARTIFACT_RELAY_API_TOKEN=new-secret\nOTHER=keep\n"
    assert "old-first" not in value
    assert "old-last" not in value


def test_secret_writer_fails_closed_when_host_writer_silently_refuses_managed_key(
    tmp_path, monkeypatch
):
    env_path = tmp_path / ".env"
    env_path.write_text("ARTIFACT_RELAY_API_TOKEN=old-secret\n")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setitem(sys.modules, "hermes_cli", SimpleNamespace())
    monkeypatch.setitem(
        sys.modules,
        "hermes_cli.managed_scope",
        SimpleNamespace(is_env_managed=lambda key: key == "ARTIFACT_RELAY_API_TOKEN"),
    )
    monkeypatch.setitem(
        sys.modules,
        "hermes_cli.config",
        SimpleNamespace(save_env_value=lambda _key, _value: None),
    )

    with pytest.raises(SetupError, match="Could not save") as failure:
        _save_token("new-secret", tmp_path)

    assert "new-secret" not in str(failure.value)
    assert env_path.read_text() == "ARTIFACT_RELAY_API_TOKEN=old-secret\n"


def test_secret_writer_fails_closed_when_host_writer_does_not_persist(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setitem(sys.modules, "hermes_cli", SimpleNamespace())
    monkeypatch.setitem(
        sys.modules,
        "hermes_cli.config",
        SimpleNamespace(save_env_value=lambda _key, _value: None),
    )

    with pytest.raises(SetupError, match="Could not save"):
        _save_token("new-secret", tmp_path)

    assert not (tmp_path / ".env").exists()


def test_loopback_device_flow_polls_and_saves_profile_credentials(tmp_path, monkeypatch):
    DeviceFlow.token_requests = 0
    DeviceFlow.bodies = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), DeviceFlow)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    messages: list[str] = []
    intervals: list[float] = []
    configured: list[str] = []
    published: list[tuple[str, str]] = []
    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    (home / ".env").write_text("OTHER_SECRET=keep-me\n")
    original_mode = stat.S_IMODE((home / ".env").stat().st_mode)
    try:
        result = hosted_setup(
            control_plane=origin,
            timeout=30,
            output=messages.append,
            sleep=intervals.append,
            config_setter=configured.append,
            smoke_publisher=lambda base_url, token: (
                published.append((base_url, token)) or f"{base_url}/a/{'A' * 32}"
            ),
        )
    finally:
        server.shutdown()
        thread.join()

    assert result is None
    assert any("ABCD-EFGH" in line for line in messages)
    assert any(f"{origin}/activate/ABCD-EFGH" in line for line in messages)
    assert DeviceFlow.api_token not in "\n".join(messages)
    assert intervals == [1, 1, 6]
    assert DeviceFlow.bodies == [
        {},
        {"device_code": "one-time-device-code"},
        {"device_code": "one-time-device-code"},
        {"device_code": "one-time-device-code"},
    ]
    assert configured == [origin]
    assert published == [(origin, DeviceFlow.api_token)]
    assert any(f"{origin}/a/{'A' * 32}" in line for line in messages)
    env_path = home / ".env"
    assert env_path.read_text() == (
        f"OTHER_SECRET=keep-me\nARTIFACT_RELAY_API_TOKEN={DeviceFlow.api_token}\n"
    )
    assert stat.S_IMODE(env_path.stat().st_mode) == original_mode
    assert any("new Hermes session" in line for line in messages)


def test_pending_authorization_stops_at_timeout_before_expiry_boundary(tmp_path):
    class Pending(DeviceFlow):
        token_requests = 0

        def do_POST(self) -> None:
            if self.path == "/api/device/token":
                type(self).token_requests += 1
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                self._json({"error": "authorization_pending"}, 400)
                return
            super().do_POST()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Pending)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    now = [0.0]

    def advance(seconds: float) -> None:
        now[0] += seconds

    try:
        with pytest.raises(SetupError, match="timed out"):
            hosted_setup(
                control_plane=f"http://127.0.0.1:{server.server_port}",
                timeout=2,
                hermes_home=tmp_path,
                output=lambda _line: None,
                sleep=advance,
                monotonic=lambda: now[0],
                config_setter=lambda _url: None,
                smoke_publisher=lambda _base_url, _token: "unused",
            )
    finally:
        server.shutdown()
        thread.join()
    assert Pending.token_requests == 1
    assert not (tmp_path / ".env").exists()


def test_token_poll_refuses_redirect_without_leaking_device_code(tmp_path):
    class Sink(BaseHTTPRequestHandler):
        body: bytes | None = None

        def do_POST(self) -> None:
            type(self).body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_error(418)

        def log_message(self, *_args: object) -> None:
            pass

    sink = ThreadingHTTPServer(("127.0.0.1", 0), Sink)

    class RedirectToken(DeviceFlow):
        def do_POST(self) -> None:
            if self.path == "/api/device/token":
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{sink.server_port}/capture")
                self.end_headers()
                return
            super().do_POST()

    source = ThreadingHTTPServer(("127.0.0.1", 0), RedirectToken)
    threads = [
        threading.Thread(target=server.serve_forever, daemon=True) for server in (sink, source)
    ]
    for thread in threads:
        thread.start()
    try:
        with pytest.raises(SetupError, match="refused an HTTP redirect") as failure:
            hosted_setup(
                control_plane=f"http://127.0.0.1:{source.server_port}",
                timeout=30,
                hermes_home=tmp_path,
                output=lambda _line: None,
                sleep=lambda _seconds: None,
                config_setter=lambda _url: None,
                smoke_publisher=lambda _base_url, _token: "unused",
            )
        assert "one-time-device-code" not in str(failure.value)
        assert Sink.body is None
    finally:
        for server in (source, sink):
            server.shutdown()
        for thread in threads:
            thread.join()


def test_foreign_verification_url_is_rejected_before_display(tmp_path):
    class ForeignVerification(DeviceFlow):
        def do_POST(self) -> None:
            if self.path == "/api/device/authorizations":
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                self._json(
                    {
                        "device_code": "one-time-device-code",
                        "user_code": "ABCD-EFGH",
                        "verification_uri_complete": "https://evil.example/activate",
                        "interval": 1,
                        "expires_in": 30,
                    }
                )
                return
            super().do_POST()

    server = ThreadingHTTPServer(("127.0.0.1", 0), ForeignVerification)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    messages: list[str] = []
    try:
        with pytest.raises(SetupError, match="verification URL"):
            hosted_setup(
                control_plane=f"http://127.0.0.1:{server.server_port}",
                timeout=30,
                hermes_home=tmp_path,
                output=messages.append,
                config_setter=lambda _url: None,
                smoke_publisher=lambda _base_url, _token: "unused",
            )
    finally:
        server.shutdown()
        thread.join()
    assert not messages


def test_same_origin_noncanonical_verification_path_is_rejected_before_display(tmp_path):
    class WrongVerificationPath(DeviceFlow):
        def do_POST(self) -> None:
            if self.path == "/api/device/authorizations":
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                origin = f"http://127.0.0.1:{self.server.server_port}"
                self._json(
                    {
                        "device_code": "one-time-device-code",
                        "user_code": "ABCD-EFGH",
                        "verification_uri_complete": (f"{origin}/login?user_code=ABCD-EFGH"),
                        "interval": 1,
                        "expires_in": 30,
                    }
                )
                return
            super().do_POST()

    server = ThreadingHTTPServer(("127.0.0.1", 0), WrongVerificationPath)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    messages: list[str] = []
    try:
        with pytest.raises(SetupError, match="verification URL"):
            hosted_setup(
                control_plane=f"http://127.0.0.1:{server.server_port}",
                timeout=30,
                hermes_home=tmp_path,
                output=messages.append,
                config_setter=lambda _url: None,
                smoke_publisher=lambda _base_url, _token: "unused",
            )
    finally:
        server.shutdown()
        thread.join()
    assert not messages


def test_non_finite_timeout_is_rejected_before_network(tmp_path):
    for timeout in (float("nan"), float("inf")):
        with pytest.raises(SetupError, match="finite"):
            hosted_setup(
                control_plane="http://127.0.0.1:1",
                timeout=timeout,
                hermes_home=tmp_path,
                output=lambda _line: None,
                config_setter=lambda _url: None,
                smoke_publisher=lambda _base_url, _token: "unused",
            )


def test_remote_plain_http_control_plane_is_rejected_before_network(tmp_path):
    with pytest.raises(SetupError, match="HTTPS origin"):
        hosted_setup(
            control_plane="http://relay.example",
            timeout=1,
            hermes_home=tmp_path,
            output=lambda _line: None,
            config_setter=lambda _url: None,
            smoke_publisher=lambda _base_url, _token: "unused",
        )
    assert not (tmp_path / ".env").exists()


def test_expired_device_code_is_reported_without_saving_secret(tmp_path):
    class Expired(DeviceFlow):
        def do_POST(self) -> None:
            if self.path == "/api/device/token":
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                self._json({"error": "expired_token", "error_description": "do not log me"}, 400)
                return
            super().do_POST()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Expired)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(SetupError, match="authorization expired") as failure:
            hosted_setup(
                control_plane=f"http://127.0.0.1:{server.server_port}",
                timeout=30,
                hermes_home=tmp_path,
                output=lambda _line: None,
                sleep=lambda _seconds: None,
                config_setter=lambda _url: None,
                smoke_publisher=lambda _base_url, _token: "unused",
            )
        assert "do not log me" not in str(failure.value)
    finally:
        server.shutdown()
        thread.join()
    assert not (tmp_path / ".env").exists()


def test_completed_initial_response_after_deadline_is_rejected_before_display(
    tmp_path, monkeypatch
):
    now = [0.0]
    messages: list[str] = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size=-1):
            return json.dumps(
                {
                    "device_code": "one-time-device-code",
                    "user_code": "ABCD-EFGH",
                    "verification_uri_complete": ("http://127.0.0.1:9999/activate/ABCD-EFGH"),
                    "interval": 1,
                    "expires_in": 30,
                }
            ).encode()

    class Opener:
        def open(self, _request, *, timeout):
            assert timeout == 0.2
            now[0] = 0.3
            return Response()

    monkeypatch.setattr("artifact_setup.urllib.request.build_opener", lambda *_args: Opener())

    with pytest.raises(SetupError, match="timed out"):
        hosted_setup(
            control_plane="http://127.0.0.1:9999",
            timeout=0.2,
            hermes_home=tmp_path,
            output=messages.append,
            monotonic=lambda: now[0],
            config_setter=lambda _url: None,
            smoke_publisher=lambda _base_url, _token: "unused",
        )

    assert not messages


def test_completed_poll_response_after_deadline_is_rejected_before_saving(tmp_path, monkeypatch):
    now = [0.0]
    socket_timeouts: list[float] = []
    responses = [
        {
            "device_code": "one-time-device-code",
            "user_code": "ABCD-EFGH",
            "verification_uri_complete": "http://127.0.0.1:9999/activate/ABCD-EFGH",
            "interval": 1,
            "expires_in": 30,
        },
        {"base_url": "http://127.0.0.1:9999", "api_token": "secret"},
    ]

    class Response:
        status = 200

        def __init__(self, value):
            self.value = value

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size=-1):
            return json.dumps(self.value).encode()

    class Opener:
        def open(self, _request, *, timeout):
            socket_timeouts.append(timeout)
            value = responses.pop(0)
            if not responses:
                now[0] = 1.3
            return Response(value)

    monkeypatch.setattr("artifact_setup.urllib.request.build_opener", lambda *_args: Opener())

    with pytest.raises(SetupError, match="timed out"):
        hosted_setup(
            control_plane="http://127.0.0.1:9999",
            timeout=1.2,
            hermes_home=tmp_path,
            output=lambda _line: None,
            sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
            monotonic=lambda: now[0],
            config_setter=lambda _url: None,
            smoke_publisher=lambda _base_url, _token: "unused",
        )

    assert socket_timeouts == pytest.approx([1.2, 0.2])
    assert not (tmp_path / ".env").exists()


def test_initial_authorization_request_is_bounded_by_overall_timeout(tmp_path):
    class StalledAuthorization(DeviceFlow):
        def do_POST(self) -> None:
            if self.path == "/api/device/authorizations":
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                time.sleep(1)
                return
            super().do_POST()

    server = ThreadingHTTPServer(("127.0.0.1", 0), StalledAuthorization)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    started = time.monotonic()
    try:
        with pytest.raises(SetupError, match="timed out"):
            hosted_setup(
                control_plane=f"http://127.0.0.1:{server.server_port}",
                timeout=0.2,
                hermes_home=tmp_path,
                output=lambda _line: None,
                config_setter=lambda _url: None,
                smoke_publisher=lambda _base_url, _token: "unused",
            )
    finally:
        elapsed = time.monotonic() - started
        server.shutdown()
        thread.join()

    assert elapsed < 0.8
    assert not (tmp_path / ".env").exists()


def test_save_base_url_bounds_hermes_subprocess_by_remaining_timeout(monkeypatch):
    observed: list[float] = []

    def stalled_run(*_args, timeout, **_kwargs):
        observed.append(timeout)
        raise subprocess.TimeoutExpired(cmd="hermes config set", timeout=timeout)

    monkeypatch.setattr("artifact_setup.subprocess.run", stalled_run)

    with pytest.raises(SetupError, match="service URL"):
        save_base_url("https://publisher.example", timeout=0.25)

    assert observed == [0.25]


def test_overall_timeout_includes_stalled_config_persistence(tmp_path, monkeypatch):
    now = [0.0]
    smoke_called = False
    responses = [
        {
            "device_code": "one-time-device-code",
            "user_code": "ABCD-EFGH",
            "verification_uri_complete": "http://127.0.0.1:9999/activate/ABCD-EFGH",
            "interval": 1,
            "expires_in": 30,
        },
        {"base_url": "http://127.0.0.1:9999", "api_token": "secret"},
    ]

    class Response:
        status = 200

        def __init__(self, value):
            self.value = value

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size=-1):
            return json.dumps(self.value).encode()

    class Opener:
        def open(self, _request, *, timeout):
            assert timeout > 0
            return Response(responses.pop(0))

    def stalled_config(_base_url: str, *, timeout: float) -> None:
        assert timeout == pytest.approx(1.0)
        now[0] += timeout + 0.1

    def smoke(_base_url: str, _token: str, *, timeout: float) -> str:
        nonlocal smoke_called
        smoke_called = True
        return "unused"

    monkeypatch.setattr("artifact_setup.urllib.request.build_opener", lambda *_args: Opener())

    with pytest.raises(SetupError, match="timed out"):
        hosted_setup(
            control_plane="http://127.0.0.1:9999",
            timeout=2,
            hermes_home=tmp_path,
            output=lambda _line: None,
            sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
            monotonic=lambda: now[0],
            config_setter=stalled_config,
            smoke_publisher=smoke,
        )

    assert smoke_called is False


def test_overall_timeout_is_forwarded_to_and_checked_after_smoke_publication(tmp_path, monkeypatch):
    now = [0.0]
    smoke_timeouts: list[float] = []
    messages: list[str] = []
    responses = [
        {
            "device_code": "one-time-device-code",
            "user_code": "ABCD-EFGH",
            "verification_uri_complete": "http://127.0.0.1:9999/activate/ABCD-EFGH",
            "interval": 1,
            "expires_in": 30,
        },
        {"base_url": "http://127.0.0.1:9999", "api_token": "secret"},
    ]

    class Response:
        status = 200

        def __init__(self, value):
            self.value = value

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size=-1):
            return json.dumps(self.value).encode()

    class Opener:
        def open(self, _request, *, timeout):
            assert timeout > 0
            return Response(responses.pop(0))

    def smoke(_base_url: str, _token: str, *, timeout: float) -> str:
        smoke_timeouts.append(timeout)
        now[0] += timeout + 0.1
        return "http://127.0.0.1:9999/a/" + "A" * 32

    monkeypatch.setattr("artifact_setup.urllib.request.build_opener", lambda *_args: Opener())

    with pytest.raises(SetupError, match="timed out"):
        hosted_setup(
            control_plane="http://127.0.0.1:9999",
            timeout=2,
            hermes_home=tmp_path,
            output=messages.append,
            sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
            monotonic=lambda: now[0],
            config_setter=lambda _url: None,
            smoke_publisher=smoke,
        )

    assert smoke_timeouts == pytest.approx([1.0])
    assert not any("Test artifact" in message for message in messages)
