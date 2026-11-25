import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_demo_runs_end_to_end() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "demo.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "multi-role approval" in result.stdout
    assert "TRIAGE_REQUESTER_CANNOT_APPROVE" in result.stdout
