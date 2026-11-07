"""Offline tests for the shared hub machinery (scripts/common/hub_core.py and scripts/validation/hub_checks.py).

No publisher is involved: these tests check storage, IDs and the shared checks on their own, so
they run in a fraction of a second and need no network or large downloads.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "common"))
sys.path.insert(0, str(ROOT / "scripts" / "validation"))

import hub_checks as checks  # noqa: E402
import hub_core  # noqa: E402

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone(timedelta(hours=-4)))


def frame(dates, series="s", values=None, geography="Canada", series_id=None):
    values = values if values is not None else [1.0] * len(dates)
    return pd.DataFrame({"date": dates, "geography": geography, "series": series, "value": values, "unit": "u",
                         "source": "test", "dataset_id": "test_x_s_monthly", "status": "", "status_category": "",
                         "geo_code": "", "series_id": series_id or f"id_{series}"})


def finish(ds, processed, frequency="monthly", **kw):
    record = {"dataset_id": ds.dataset_id, "observation_end": max(processed["date"])}
    return hub_core.finish_dataset(ds, processed=processed, frequency=frequency, results=[], now=kw.pop("now", NOW),
                                   record=record, source_last_updated=None, schema={}, schema_version=1,
                                   rows_raw=len(processed), coverage="Canada", validator="v", ai_use_note="n", **kw)


# ----------------------------------------------------------------------------
# Storage and IDs
# ----------------------------------------------------------------------------

def test_raw_versions_are_kept_and_never_overwritten(tmp_path):
    first = hub_core.open_dataset(tmp_path, "test", "test_x_s_monthly", "20261024", b"a", ".json")
    finish(first, frame(["2026-09-01"]))
    same = hub_core.open_dataset(tmp_path, "test", "test_x_s_monthly", "20261024", b"a", ".json")
    assert not same.raw_written and same.raw_path == first.raw_path
    finish(same, frame(["2026-09-01"]))
    changed = hub_core.open_dataset(tmp_path, "test", "test_x_s_monthly", "20261024", b"b", ".json")
    assert changed.raw_written and changed.raw_path.name == "test_x_s_monthly_20261024_2.json"
    assert first.raw_path.read_bytes() == b"a"


def test_companion_files_are_saved_next_to_the_raw_file(tmp_path):
    ds = hub_core.open_dataset(tmp_path, "test", "test_x_s_monthly", "20261024", b"data", ".csv",
                               {"_cube_metadata.csv": b"meta"})
    assert ds.raw_path.with_name("test_x_s_monthly_20261024_cube_metadata.csv").read_bytes() == b"meta"


def test_existing_record_pins_the_dataset_id(tmp_path):
    (tmp_path / "metadata").mkdir()
    (tmp_path / "metadata" / "test_123_old_subject_annual.json").write_text("{}", encoding="utf-8")
    dataset_id, note = hub_core.resolve_dataset_id(tmp_path / "metadata", "test_123_", "test_123_new_subject_annual", "x")
    assert dataset_id == "test_123_old_subject_annual" and "kept existing dataset ID" in note


def test_two_records_for_one_source_id_stop_the_run(tmp_path):
    (tmp_path / "metadata").mkdir()
    for name in ["test_123_a_annual", "test_123_b_annual"]:
        (tmp_path / "metadata" / f"{name}.json").write_text("{}", encoding="utf-8")
    with pytest.raises(hub_core.AcquisitionError, match="more than one metadata record"):
        hub_core.resolve_dataset_id(tmp_path / "metadata", "test_123_", "test_123_a_annual", "x")


def test_failed_validation_leaves_previous_outputs_and_history(tmp_path):
    ds = hub_core.open_dataset(tmp_path, "test", "test_x_s_monthly", "20261024", b"a")
    finish(ds, frame(["2026-09-01"]))
    before = ds.metadata_path.read_bytes(), ds.processed_path.read_bytes()
    clash = pd.concat([frame(["2026-09-01"]), frame(["2026-09-01"], values=[2.0])])
    later = hub_core.open_dataset(tmp_path, "test", "test_x_s_monthly", "20261025", b"b")
    with pytest.raises(hub_core.AcquisitionError, match="validation failed"):
        finish(later, clash)
    assert (ds.metadata_path.read_bytes(), ds.processed_path.read_bytes()) == before
    assert later.raw_path.exists()  # the new raw file is kept for inspection


def test_metadata_history_grows_with_each_retrieval(tmp_path):
    for day, data in [("20261024", b"a"), ("20261025", b"a"), ("20261026", b"b")]:
        finish(hub_core.open_dataset(tmp_path, "test", "test_x_s_monthly", day, data), frame(["2026-09-01"]))
    meta = json.loads((tmp_path / "metadata" / "test_x_s_monthly.json").read_text(encoding="utf-8"))
    assert [h["data_changed"] for h in meta["retrieval_history"]] == [None, False, True]
    assert meta["checksum"] == hub_core.sha256_of(b"b")


# ----------------------------------------------------------------------------
# Shared checks added in Week 3
# ----------------------------------------------------------------------------

def test_revisions_report_changed_and_vanished_values():
    old = frame(["2026-07-01", "2026-08-01", "2026-09-01"], values=[1.0, 2.0, 3.0])
    new = frame(["2026-08-01", "2026-09-01", "2026-10-01"], values=[2.0, 3.5, 4.0])
    result = checks.check_revisions(old, new)
    assert result["status"] == "info"  # QA-RV1 is INFO in the team standard
    assert (result["changed_values"], result["removed_observations"], result["new_observations"]) == (1, 1, 1)


def test_revisions_pass_when_only_new_observations_arrive():
    old = frame(["2026-08-01"], values=[2.0])
    result = checks.check_revisions(old, frame(["2026-08-01", "2026-09-01"], values=[2.0, float("nan")]))
    assert result["status"] == "pass" and result["new_observations"] == 1


def test_daily_gaps_flag_stretches_longer_than_a_week():
    dates = pd.bdate_range("2026-09-01", "2026-09-30").strftime("%Y-%m-%d").tolist()
    assert checks.check_date_gaps(frame(dates), "daily")["status"] == "pass"  # weekends are fine
    holed = [d for d in dates if not "2026-09-08" <= d <= "2026-09-18"]
    result = checks.check_date_gaps(frame(holed), "daily")
    assert result["status"] == "warn" and result["longest_gap_days"] == 14


def test_freshness_warns_on_stale_data():
    assert checks.check_freshness("2026-10-22", "daily", NOW)["status"] == "pass"
    assert checks.check_freshness("2026-09-01", "monthly", NOW)["status"] == "pass"
    stale = checks.check_freshness("2023-01-01", "annual", NOW)
    assert stale["status"] == "warn" and stale["age_days"] > stale["limit_days"]
    assert checks.check_freshness("2020-01-01", "irregular", NOW)["status"] == "pass"  # not checked


def test_finish_records_revisions_against_the_previous_processed_file(tmp_path):
    finish(hub_core.open_dataset(tmp_path, "test", "test_x_s_monthly", "20261024", b"a"),
           frame(["2026-08-01", "2026-09-01"], values=[1.0, 2.0]))
    result = finish(hub_core.open_dataset(tmp_path, "test", "test_x_s_monthly", "20261025", b"b"),
                    frame(["2026-08-01", "2026-09-01"], values=[1.0, 2.5]))
    # A revision is a note (INFO, QA-RV1 in the team standard), not a warning.
    assert any(n.startswith("revisions: 1 values revised") for n in result["notes"])


# ----------------------------------------------------------------------------
# Fields added for the team's metadata standards (W3-SOL-03)
# ----------------------------------------------------------------------------

def test_reproducibility_is_confirmed_by_two_identical_retrievals(tmp_path):
    finish(hub_core.open_dataset(tmp_path, "test", "test_x_s_monthly", "20261024", b"a"), frame(["2026-09-01"]))
    meta = json.loads((tmp_path / "metadata" / "test_x_s_monthly.json").read_text(encoding="utf-8"))
    assert meta["reproducibility_rating"]["rating"] == "Potential High"
    finish(hub_core.open_dataset(tmp_path, "test", "test_x_s_monthly", "20261025", b"a"), frame(["2026-09-01"]),
           now=NOW + timedelta(days=1))
    meta = json.loads((tmp_path / "metadata" / "test_x_s_monthly.json").read_text(encoding="utf-8"))
    assert meta["reproducibility_rating"]["rating"] == "Confirmed High"
    assert "2026-10-24, 2026-10-25" in meta["reproducibility_rating"]["basis"]


def test_catalogue_fields_are_copied_and_blanks_become_null(tmp_path):
    governance = hub_core.governance_from({"update_frequency": " monthly ", "priority": "", "extra": "ignored"})
    finish(hub_core.open_dataset(tmp_path, "test", "test_x_s_monthly", "20261024", b"a"), frame(["2026-09-01"]),
           governance=governance)
    meta = json.loads((tmp_path / "metadata" / "test_x_s_monthly.json").read_text(encoding="utf-8"))
    assert meta["update_frequency"] == "monthly"
    assert all(meta[f] is None for f in ["refresh_schedule", "revision_policy", "priority", "known_limitations"])
    assert meta["primary_key"] == ["date", "geography", "series_id"] and "extra" not in meta


# ----------------------------------------------------------------------------
# Week 5: stable series IDs, status categories, the team standard's reproducibility rule
# ----------------------------------------------------------------------------

def test_series_identity_fails_when_an_id_has_two_labels():
    clash = pd.concat([frame(["2026-08-01"], series="a", series_id="v1"), frame(["2026-09-01"], series="b", series_id="v1")])
    assert checks.check_series_identity(clash)["status"] == "fail"
    ok = pd.concat([frame(["2026-08-01"], series="a", series_id="v1"), frame(["2026-08-01"], series="b", series_id="v2")])
    assert checks.check_series_identity(ok)["status"] == "pass"
    assert checks.check_series_identity(frame(["2026-08-01"], series_id=" "))["status"] == "warn"


def test_status_symbols_get_a_standard_category_and_the_symbol_is_kept():
    status = pd.Series(["", "..", "x", "F", "E", "ZZ"])
    assert checks.status_category(status).tolist() == ["", "not_available", "suppressed", "unreliable",
                                                       "quality_flag", "unknown"]


def test_missing_values_all_explained_are_info_not_pass():
    explained = frame(["2026-08-01", "2026-09-01"], values=[1.0, float("nan")]).assign(status=["", ".."])
    assert checks.check_missing(explained, {"..": "not available"})["status"] == "info"


def test_revisions_follow_the_series_id_when_a_label_changes():
    old = frame(["2026-08-01", "2026-09-01"], series="Old label", series_id="v1", values=[1.0, 2.0])
    new = frame(["2026-08-01", "2026-09-01"], series="New label", series_id="v1", values=[1.0, 2.0])
    result = checks.check_revisions(old, new)
    assert result["matched_on"] == "series_id"
    assert (result["changed_values"], result["removed_observations"], result["relabelled_series"]) == (0, 0, 1)


def test_reproducibility_needs_identical_data_on_two_different_days(tmp_path):
    for day, now in [("20261024", NOW), ("20261024", NOW + timedelta(hours=2))]:
        finish(hub_core.open_dataset(tmp_path, "test", "test_x_s_monthly", day, b"a"), frame(["2026-09-01"]), now=now)
    meta = json.loads((tmp_path / "metadata" / "test_x_s_monthly.json").read_text(encoding="utf-8"))
    assert meta["reproducibility_rating"]["rating"] == "Potential High"  # same day does not count
    finish(hub_core.open_dataset(tmp_path, "test", "test_x_s_monthly", "20261025", b"a"), frame(["2026-09-01"]),
           now=NOW + timedelta(days=1))
    meta = json.loads((tmp_path / "metadata" / "test_x_s_monthly.json").read_text(encoding="utf-8"))
    assert meta["reproducibility_rating"]["rating"] == "Confirmed High"


def test_team_standard_checks_run_on_every_retrieval(tmp_path):
    finish(hub_core.open_dataset(tmp_path, "test", "test_x_s_monthly", "20261024", b"a"), frame(["2026-09-01"]))
    meta = json.loads((tmp_path / "metadata" / "test_x_s_monthly.json").read_text(encoding="utf-8"))
    names = {c["check"] for c in meta["validation"]["checks"]}
    assert {"key_fields", "missing_share", "metadata_completeness", "series_identity", "future_dates"} <= names
    assert meta["validation"]["standard"] == "RAI_Metadata_QA_Standard_v1.0"
    assert meta["primary_key"] == ["date", "geography", "series_id"]
