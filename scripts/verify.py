"""Run the same checks expected in CI: lint, tests and golden eval."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    commands = [
        ("ruff", [sys.executable, "-m", "ruff", "check", "."]),
        ("pytest", [sys.executable, "-m", "pytest", "-q"]),
        ("golden eval", [sys.executable, str(ROOT / "scripts" / "eval.py")]),
    ]
    failures: list[str] = []
    for name, command in commands:
        print(f"\n=== {name} ===", flush=True)
        result = subprocess.run(command, cwd=ROOT)
        if result.returncode != 0:
            failures.append(name)
    print("\n=== summary ===", flush=True)
    if failures:
        print("FAILED: " + ", ".join(failures), flush=True)
        return 1
    print("PASS: lint, tests and golden eval", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
