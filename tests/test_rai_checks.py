"""Offline tests for the RAI add-on checks (scripts/validation/rai_checks.py). No network needed."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "validation"))
import rai_checks as rc  # noqa: E402


def frame(values, series="s", unit="Percent", dates=None, status=""):
    dates = dates or pd.date_range("2020-01-01", periods=len(values), freq="MS").strftime("%Y-%m-%d")
    return pd.DataFrame({"date": list(dates), "geography": "Canada", "series": series, "value": values, "unit": unit,
                         "source": "t", "dataset_id": "t_1_x_monthly", "status": status})


def test_key_fields_fail_on_empty_series():
    f = frame([1.0, 2.0]); f.loc[0, "series"] = ""
    assert rc.check_key_fields(f)["status"] == "fail"
    assert rc.check_key_fields(frame([1.0, 2.0]))["status"] == "pass"


def test_missing_share_thresholds():
    assert rc.check_missing_share(frame([1.0, 2.0]))["status"] == "pass"
    f = frame([1.0] * 99 + [np.nan]); assert rc.check_missing_share(f)["status"] == "info"       # 1%
    f = frame([1.0] * 80 + [np.nan] * 20); assert rc.check_missing_share(f)["status"] == "warn"  # 20%


def test_value_range_flags_percent_outside_bounds():
    assert rc.check_value_range(frame([5.0, 120.0]))["status"] == "warn"
    assert rc.check_value_range(frame([5.0, 6.0]))["status"] == "pass"
    assert rc.check_value_range(frame([1.0, 2.0], unit="Dollars"))["status"] == "pass"


def test_percent_convention_flags_decimals():
    assert rc.check_percent_convention(frame([0.05] * 20))["status"] == "warn"
    assert rc.check_percent_convention(frame([5.2] * 20))["status"] == "pass"


def test_period_change_flags_a_jump():
    values = list(np.linspace(5, 5.5, 60)); values[30] = 25.0
    r = rc.check_period_change(frame(values))
    assert r["status"] in ("info", "warn") and r["flagged"] >= 1
    assert rc.check_period_change(frame(list(np.linspace(5, 5.5, 60))))["status"] == "pass"


def test_status_symbols_unknown_warns():
    f = frame([1.0, 2.0], status=["A", "ZZ"])
    assert rc.check_status_symbols(f, {"A": "ok"})["status"] == "warn"
    assert rc.check_status_symbols(frame([1.0], status=["E"]), {"E": "caution"})["status"] == "info"


def test_unit_consistency_fails_on_two_units():
    f = frame([1.0, 2.0]); f.loc[1, "unit"] = "Dollars"
    assert rc.check_unit_consistency(f)["status"] == "fail"


def test_future_dates():
    assert rc.check_future_dates(frame([1.0], dates=["2030-01-01"]), datetime(2026, 11, 1))["status"] == "fail"
    assert rc.check_future_dates(frame([1.0], dates=["2026-01-01"]), datetime(2026, 11, 1))["status"] == "pass"


def test_metadata_completeness():
    full = {k: f"value_{k}" for k in rc.REQUIRED_KEYS}
    assert rc.check_metadata_completeness(full)["status"] in ("pass", "info")
    bad = dict(full, known_limitations="", description="x", dataset_name="x")
    r = rc.check_metadata_completeness(bad)
    assert r["status"] == "warn" and "known_limitations" in r["missing"] and "repeats" in r["detail"]


def test_metadata_consistency_end_date():
    f = frame([1.0, 2.0], dates=["2026-01-01", "2026-02-01"])
    assert rc.check_metadata_consistency(f, {"dataset_id": "t_1_x_monthly", "observation_end": "2026-02-01"})["status"] == "pass"
    assert rc.check_metadata_consistency(f, {"dataset_id": "t_1_x_monthly", "observation_end": "2026-03-01"})["status"] == "warn"


def test_event_table_checks():
    d = pd.DataFrame({"a": ["Unknown / N/A"] * 50 + ["x"] * 50, "b": ["k"] * 100, "m": ["Oil"] * 5 + ["OIL"] * 95,
                      "r": ["Land; Water"] * 3 + ["Land"] * 97, "amt": [0] * 80 + [5] * 20, "u": ["other"] * 100})
    assert rc.check_text_sentinels(d, ["a"])["status"] == "warn"
    assert rc.check_constant_columns(d, ["b"])["status"] == "warn"
    assert rc.check_category_variants(d, ["m"])["status"] == "warn"
    assert rc.check_multi_valued(d, ["r"])["columns"] == {"r": 3}
    assert rc.check_zero_as_missing(d, "amt", "u")["status"] == "warn"


def test_runner_and_log_rows():
    results = rc.run_rai_checks(frame([5.0, 5.1, 5.2]), now=datetime(2026, 11, 1))
    assert {r["check"] for r in results} >= {"key_fields", "missing_share", "value_range", "future_dates"}
    rows = rc.to_log_rows("t_1_x_monthly", results, "2026-11-01")
    assert rows[0]["check_id"] == "QA-CMP1" and rows[0]["severity"] == "PASS"
