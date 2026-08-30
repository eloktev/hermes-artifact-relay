"""Hermes Artifact Relay plugin registration."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

try:
    from .artifact_plugin import (
        ArtifactClient,
        ArtifactError,
        requirements_available,
        session_metadata,
    )
except ImportError:  # pragma: no cover - direct source loading compatibility
    from artifact_plugin import (
        ArtifactClient,
        ArtifactError,
        requirements_available,
        session_metadata,
    )

try:
    from .artifact_setup import DEFAULT_CONTROL_PLANE, SetupError, hosted_setup
except ImportError:  # pragma: no cover - direct source loading compatibility
    from artifact_setup import DEFAULT_CONTROL_PLANE, SetupError, hosted_setup

_base_url = ""
_include_provenance = False
_CONFIG_REMEDIATION = (
    "Artifact Relay is unavailable. Configure base_url with: hermes config set "
    "plugins.entries.artifact-relay.settings.base_url https://publisher.example"
)


def _result(success: bool, **payload: Any) -> str:
    return json.dumps({"success": success, **payload}, ensure_ascii=False)


def _client() -> ArtifactClient:
    if not _base_url:
        raise ArtifactError(_CONFIG_REMEDIATION)
    return ArtifactClient(_base_url)


def _read(args: dict[str, Any], **_kwargs: Any) -> str:
    try:
        return _result(True, artifact=_client().read(str(args.get("url", ""))))
    except (ArtifactError, OSError, TypeError, ValueError) as exc:
        return _result(False, error=str(exc))


def _publish(args: dict[str, Any], **kwargs: Any) -> str:
    try:
        provenance = session_metadata(kwargs.get("session_id")) if _include_provenance else {}
        if _include_provenance and not provenance.get("session_title") and kwargs.get("user_task"):
            provenance["session_title"] = str(kwargs["user_task"])[:512]
        artifact = _client().publish(
            title=str(args.get("title", "")),
            content=str(args.get("content", "")),
            summary=str(args.get("summary", "")),
            fmt=str(args.get("format", "markdown")),
            expires_days=int(args.get("expires_days", 30)),
            provenance=provenance,
        )
        return _result(True, artifact=artifact)
    except (ArtifactError, OSError, TypeError, ValueError) as exc:
        return _result(False, error=str(exc))


def _setup_cli(parser: Any) -> None:
    """Build the operator-facing command tree."""
    commands = parser.add_subparsers(dest="artifact_relay_action", required=True)
    setup = commands.add_parser("setup", help="Connect to the hosted Artifact Relay")
    setup.add_argument("--control-plane", default=DEFAULT_CONTROL_PLANE)
    setup.add_argument("--timeout", type=float, default=600)
    commands.add_parser("status", help="Show credential-safe configuration status")


def _cli_command(args: Any) -> int:
    """Dispatch Artifact Relay operator commands without exposing credentials."""
    if args.artifact_relay_action == "status":
        print(f"Service URL: {'configured' if _base_url else 'not configured'}")
        print(f"API token: {'configured' if requirements_available() else 'not configured'}")
        ready = bool(_base_url) and requirements_available()
        print(f"Tools: {'ready in a new session' if ready else 'unavailable'}")
        return 0
    try:
        hosted_setup(control_plane=args.control_plane, timeout=args.timeout)
    except SetupError as exc:
        print(f"Artifact Relay setup failed: {exc}", file=sys.stderr)
        return 1
    return 0


def register(ctx: Any) -> None:
    """Register two gated tools and the bundled publishing skill."""
    global _base_url, _include_provenance
    _base_url = str(ctx.get_config("base_url", default="") or "").rstrip("/")
    _include_provenance = bool(ctx.get_config("include_provenance", default=False))

    def available() -> bool:
        return bool(_base_url) and requirements_available()

    ctx.register_tool(
        name="artifact_read",
        toolset="artifact_relay",
        schema={
            "name": "artifact_read",
            "description": (
                "Read original Markdown or HTML from a configured Artifact Relay URL. "
                "Foreign-origin URLs are rejected before authentication."
            ),
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "Artifact URL or id"}},
                "required": ["url"],
            },
        },
        handler=_read,
        check_fn=available,
        requires_env=["ARTIFACT_RELAY_API_TOKEN"],
    )
    ctx.register_tool(
        name="artifact_publish",
        toolset="artifact_relay",
        schema={
            "name": "artifact_publish",
            "description": (
                "Publish private Markdown or standalone HTML to the configured Artifact Relay "
                "and return its viewer URL. Never include secrets."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Artifact title"},
                    "content": {
                        "type": "string",
                        "description": "Complete Markdown or standalone HTML source",
                    },
                    "summary": {"type": "string", "default": ""},
                    "format": {
                        "type": "string",
                        "enum": ["markdown", "html"],
                        "default": "markdown",
                    },
                    "expires_days": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 3650,
                        "default": 30,
                    },
                },
                "required": ["title", "content"],
            },
        },
        handler=_publish,
        check_fn=available,
        requires_env=["ARTIFACT_RELAY_API_TOKEN"],
    )
    skill = Path(__file__).parent / "skills" / "artifact-publishing" / "SKILL.md"
    ctx.register_skill(
        "artifact-publishing",
        skill,
        "Publish long results through the configured Artifact Relay.",
    )
    ctx.register_cli_command(
        name="artifact-relay",
        help="Set up and inspect the hosted Artifact Relay",
        setup_fn=_setup_cli,
        handler_fn=_cli_command,
        description="Connect Hermes to the hosted Artifact Relay without exposing credentials.",
    )
