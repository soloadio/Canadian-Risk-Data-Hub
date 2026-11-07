"""The mentor/demo package runs end to end and reproduces its expected outputs exactly (offline, a few seconds)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "demo"))

import run_demo  # noqa: E402


def test_demo_runs_end_to_end_and_matches_expected_outputs(tmp_path, capsys):
    assert run_demo.main(["--output", str(tmp_path / "demo_hub")]) == 0
    assert "DEMO PASSED" in capsys.readouterr().out
