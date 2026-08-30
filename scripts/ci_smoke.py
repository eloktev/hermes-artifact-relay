#!/usr/bin/env python3
"""Exercise real Hermes plugin doctor and Git installation in an isolated profile."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def run(command: list[str], *, env: dict[str, str]) -> None:
    subprocess.run(command, check=True, env=env)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: ci_smoke.py PATH_TO_HERMES_CHECKOUT", file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parents[1]
    hermes_checkout = Path(sys.argv[1]).resolve()
    prefix = ["uv", "run", "--frozen", "--project", str(hermes_checkout), "hermes"]
    env = dict(os.environ)
    env["ARTIFACT_RELAY_API_TOKEN"] = "ci-non-production-token"

    run([*prefix, "plugins", "doctor", str(root), "--ci"], env=env)
    with tempfile.TemporaryDirectory(prefix="hermes-plugin-ci-") as home:
        env["HERMES_HOME"] = home
        run(
            [*prefix, "plugins", "install", root.as_uri(), "--enable"],
            env=env,
        )
        run([*prefix, "plugins", "doctor", "artifact-relay", "--ci"], env=env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
