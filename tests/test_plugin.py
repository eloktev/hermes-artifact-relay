from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

from artifact_plugin import session_metadata

ROOT = Path(__file__).resolve().parents[1]


def load_plugin():
    spec = importlib.util.spec_from_file_location(
        "artifact_relay_plugin",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class Context:
    def __init__(
        self,
        base_url: str = "https://publisher.example",
        *,
        include_provenance: bool = False,
    ) -> None:
        self.base_url = base_url
        self.include_provenance = include_provenance
        self.tools: list[dict] = []
        self.skills: list[tuple] = []
        self.cli_commands: list[dict] = []

    def get_config(self, key: str, default=None):
        if key == "base_url":
            return self.base_url or default
        if key == "include_provenance":
            return self.include_provenance
        raise AssertionError(key)

    def register_tool(self, **kwargs):
        self.tools.append(kwargs)

    def register_skill(self, *args, **kwargs):
        self.skills.append((args, kwargs))

    def register_cli_command(self, **kwargs):
        self.cli_commands.append(kwargs)


def test_register_uses_plugin_config_and_exposes_exactly_two_tools(monkeypatch):
    monkeypatch.setenv("ARTIFACT_RELAY_API_TOKEN", "token")
    module = load_plugin()
    context = Context("https://configured.example")
    module.register(context)

    assert {tool["name"] for tool in context.tools} == {
        "artifact_publish",
        "artifact_read",
    }
    assert {tool["toolset"] for tool in context.tools} == {"artifact_relay"}
    publish = next(tool for tool in context.tools if tool["name"] == "artifact_publish")
    description = publish["schema"]["description"]
    assert "Artifact Relay" in description
    assert "Artifact " + "Publisher" not in description
    assert all(tool["check_fn"]() for tool in context.tools)
    assert module._base_url == "https://configured.example"


def test_register_bundles_namespaced_skill():
    module = load_plugin()
    context = Context()
    module.register(context)
    assert len(context.skills) == 1
    args, _kwargs = context.skills[0]
    assert args[0] == "artifact-publishing"
    assert args[1] == ROOT / "skills" / "artifact-publishing" / "SKILL.md"


def test_register_exposes_artifact_relay_operator_cli():
    module = load_plugin()
    context = Context("")
    module.register(context)
    assert len(context.cli_commands) == 1
    command = context.cli_commands[0]
    assert command["name"] == "artifact-relay"
    assert callable(command["setup_fn"])
    assert callable(command["handler_fn"])


def test_operator_cli_has_setup_and_safe_status_subcommands():
    module = load_plugin()
    parser = argparse.ArgumentParser()
    module._setup_cli(parser)

    setup = parser.parse_args(
        ["setup", "--control-plane", "http://127.0.0.1:9999", "--timeout", "12"]
    )
    assert setup.artifact_relay_action == "setup"
    assert setup.control_plane == "http://127.0.0.1:9999"
    assert setup.timeout == 12
    status = parser.parse_args(["status"])
    assert status.artifact_relay_action == "status"


def test_status_reports_readiness_without_printing_secret(monkeypatch, capsys):
    module = load_plugin()
    module._base_url = "https://relay.example"
    monkeypatch.setenv("ARTIFACT_RELAY_API_TOKEN", "must-not-appear")

    result = module._cli_command(argparse.Namespace(artifact_relay_action="status"))

    assert result == 0
    output = capsys.readouterr().out
    assert "Service URL: configured" in output
    assert "API token: configured" in output
    assert "must-not-appear" not in output


def test_setup_dispatches_options_and_redacts_failures(monkeypatch, capsys):
    module = load_plugin()
    calls = []

    def fail(**kwargs):
        calls.append(kwargs)
        raise module.SetupError("authorization timed out")

    monkeypatch.setattr(module, "hosted_setup", fail)
    args = argparse.Namespace(
        artifact_relay_action="setup",
        control_plane="http://127.0.0.1:9999",
        timeout=12,
    )
    assert module._cli_command(args) == 1
    assert calls == [{"control_plane": "http://127.0.0.1:9999", "timeout": 12}]
    assert capsys.readouterr().err.strip() == "Artifact Relay setup failed: authorization timed out"


def test_missing_token_makes_tools_unavailable(monkeypatch):
    monkeypatch.delenv("ARTIFACT_RELAY_API_TOKEN", raising=False)
    module = load_plugin()
    context = Context()
    module.register(context)
    assert not any(tool["check_fn"]() for tool in context.tools)


def test_publish_does_not_export_provenance_by_default(monkeypatch):
    monkeypatch.setenv("ARTIFACT_RELAY_API_TOKEN", "token")
    module = load_plugin()
    context = Context()
    module.register(context)
    captured = {}

    class Client:
        def publish(self, **kwargs):
            captured.update(kwargs)
            return {"id": "A" * 32, "url": f"https://publisher.example/a/{'A' * 32}"}

    monkeypatch.setattr(module, "_client", Client)
    monkeypatch.setattr(
        module,
        "session_metadata",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("provenance read")),
    )
    publish = next(tool for tool in context.tools if tool["name"] == "artifact_publish")
    result = json.loads(
        publish["handler"](
            {"title": "Report", "content": "# Body"},
            session_id="session-1",
            user_task="private user task",
        )
    )
    assert result["success"] is True
    assert captured["provenance"] == {}


def test_missing_base_url_returns_safe_remediation(monkeypatch):
    monkeypatch.setenv("ARTIFACT_RELAY_API_TOKEN", "token")
    module = load_plugin()
    context = Context("")
    module.register(context)
    result = json.loads(context.tools[0]["handler"]({"url": "A" * 32}))
    assert result == {
        "success": False,
        "error": (
            "Artifact Relay is unavailable. Configure base_url with: hermes config set "
            "plugins.entries.artifact-relay.settings.base_url https://publisher.example"
        ),
    }


def test_session_metadata_is_nonessential_when_database_is_missing(tmp_path):
    assert session_metadata("session-1", state_db=tmp_path / "missing.db") == {
        "session_id": "session-1"
    }


def test_session_metadata_reads_generic_session_provenance(tmp_path):
    db = tmp_path / "state.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, source TEXT, "
            "thread_id TEXT, display_name TEXT, origin_json TEXT)"
        )
        conn.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
            (
                "session-1",
                "Architecture report",
                "discord",
                "thread-9",
                "Engineering",
                json.dumps({"topic_name": "Architecture"}),
            ),
        )
    assert session_metadata("session-1", state_db=db) == {
        "session_id": "session-1",
        "session_title": "Architecture report",
        "platform": "discord",
        "chat_name": "Engineering",
        "topic_id": "thread-9",
        "topic_name": "Architecture",
    }
