from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from artifact_plugin import ArtifactClient, ArtifactError, environment_token


class FakePublisher(BaseHTTPRequestHandler):
    token = "test-token"
    artifact_id = "A" * 32
    last_post: bytes | None = None

    def do_GET(self) -> None:
        if self.path != f"/api/artifacts/{self.artifact_id}":
            self.send_error(404)
            return
        if self.headers.get("Authorization") != f"Bearer {self.token}":
            self.send_error(401)
            return
        self._json({"title": "Report", "content": "# Source\n"})

    def do_POST(self) -> None:
        if self.path != "/api/artifacts":
            self.send_error(404)
            return
        if self.headers.get("Authorization") != f"Bearer {self.token}":
            self.send_error(401)
            return
        type(self).last_post = self.rfile.read(int(self.headers["Content-Length"]))
        self._json(
            {
                "id": self.artifact_id,
                "url": f"http://{self.headers['Host']}/a/{self.artifact_id}",
            },
            201,
        )

    def _json(self, value: dict[str, object], status: int = 200) -> None:
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        pass


@pytest.fixture
def publisher_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakePublisher)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()


def test_environment_token_uses_only_documented_secret(monkeypatch):
    monkeypatch.setenv("ARTIFACT_RELAY_API_TOKEN", " portable-token ")
    assert environment_token() == "portable-token"


def test_environment_token_returns_safe_remediation_when_missing(monkeypatch):
    monkeypatch.delenv("ARTIFACT_RELAY_API_TOKEN", raising=False)
    with pytest.raises(ArtifactError, match=r"Set ARTIFACT_RELAY_API_TOKEN"):
        environment_token()


def test_read_accepts_configured_origin_and_returns_source(publisher_server):
    client = ArtifactClient(publisher_server, token_provider=lambda: FakePublisher.token)
    result = client.read(f"{publisher_server}/a/{FakePublisher.artifact_id}")
    assert result["content"] == "# Source\n"


def test_read_rejects_foreign_origin_before_token_lookup(publisher_server):
    called = False

    def token() -> str:
        nonlocal called
        called = True
        return FakePublisher.token

    client = ArtifactClient(publisher_server, token_provider=token)
    with pytest.raises(ValueError, match="configured publisher origin"):
        client.read(f"https://evil.example/a/{FakePublisher.artifact_id}")
    assert called is False


def test_read_does_not_follow_redirects_with_bearer_token():
    class Sink(BaseHTTPRequestHandler):
        authorization: str | None = None

        def do_GET(self) -> None:
            type(self).authorization = self.headers.get("Authorization")
            self.send_error(418)

        def log_message(self, *_args: object) -> None:
            pass

    sink = ThreadingHTTPServer(("127.0.0.1", 0), Sink)

    class RedirectingPublisher(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{sink.server_port}/capture")
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            pass

    publisher = ThreadingHTTPServer(("127.0.0.1", 0), RedirectingPublisher)
    threads = [
        threading.Thread(target=server.serve_forever, daemon=True) for server in (sink, publisher)
    ]
    for thread in threads:
        thread.start()
    try:
        client = ArtifactClient(
            f"http://127.0.0.1:{publisher.server_port}",
            token_provider=lambda: FakePublisher.token,
        )
        with pytest.raises(ArtifactError, match="HTTP 302"):
            client.read(FakePublisher.artifact_id)
        assert Sink.authorization is None
    finally:
        for server in (publisher, sink):
            server.shutdown()
        for thread in threads:
            thread.join()


def test_publish_sends_content_and_provenance(publisher_server):
    FakePublisher.last_post = None
    client = ArtifactClient(publisher_server, token_provider=lambda: FakePublisher.token)
    result = client.publish(
        title="Portable report",
        content="# Body\n",
        summary="Preview",
        provenance={"session_id": "session-123", "platform": "discord"},
    )
    assert result["url"].endswith(f"/a/{FakePublisher.artifact_id}")
    assert FakePublisher.last_post is not None
    for marker in (b"Portable report", b"# Body\n", b"session-123", b"discord"):
        assert marker in FakePublisher.last_post


def test_publish_forwards_explicit_http_timeout(monkeypatch):
    observed: list[float] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size=-1):
            return json.dumps(
                {
                    "id": "A" * 32,
                    "url": f"https://publisher.example/a/{'A' * 32}",
                }
            ).encode()

    class Opener:
        def open(self, _request, *, timeout):
            observed.append(timeout)
            return Response()

    monkeypatch.setattr("artifact_plugin.urllib.request.build_opener", lambda *_args: Opener())
    client = ArtifactClient("https://publisher.example", token_provider=lambda: "secret")

    client.publish(title="Report", content="# Body\n", timeout=1.25)

    assert observed == [1.25]


def test_publish_rejects_foreign_viewer_url(monkeypatch):
    client = ArtifactClient("https://publisher.example", token_provider=lambda: "token")
    monkeypatch.setattr(
        client,
        "_request",
        lambda _request, **_kwargs: {
            "id": "A" * 32,
            "url": f"https://evil.example/a/{'A' * 32}",
        },
    )
    with pytest.raises(ArtifactError, match="unexpected artifact URL"):
        client.publish(title="Report", content="# Body\n")


@pytest.mark.parametrize(
    "base_url",
    [
        "",
        "ftp://example.com",
        "https:///missing-host",
        "https://user:password@example.com",
        "https://example.com/root",
        "https://example.com?query=1",
        "http://example.com",
    ],
)
def test_client_rejects_invalid_base_url(base_url):
    with pytest.raises(ValueError, match=r"publisher base URL"):
        ArtifactClient(base_url)
