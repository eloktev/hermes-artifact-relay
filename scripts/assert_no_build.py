#!/usr/bin/env python3
"""Fail CI if the Git-only plugin accidentally becomes a Python package."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="plugin-build-rejection-") as output:
        result = subprocess.run(
            ["uv", "build", "--out-dir", output],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        artifacts = [
            path
            for path in Path(output).iterdir()
            if path.suffix == ".whl" or path.name.endswith((".tar.gz", ".zip"))
        ]
    if result.returncode == 0 or artifacts:
        print("Git-only plugin unexpectedly produced Python package artifacts")
        return 1
    print("Python package build correctly rejected; distribute through Hermes Git install only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
