"""PEP 517 backend that rejects unsupported Python package distribution."""

from __future__ import annotations

from typing import NoReturn

_MESSAGE = (
    "hermes-artifact-relay is a Git-installed Hermes plugin, not a Python package; "
    "use `hermes plugins install`"
)


def _reject() -> NoReturn:
    raise RuntimeError(_MESSAGE)


def build_wheel(
    wheel_directory: str,
    config_settings: dict[str, object] | None = None,
    metadata_directory: str | None = None,
) -> NoReturn:
    _reject()


def build_sdist(sdist_directory: str, config_settings: dict[str, object] | None = None) -> NoReturn:
    _reject()


def prepare_metadata_for_build_wheel(
    metadata_directory: str, config_settings: dict[str, object] | None = None
) -> NoReturn:
    _reject()
