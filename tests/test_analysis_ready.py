"""Offline tests for the analysis-ready checks and the promotion workflow.

A tiny hub is written to a temporary folder: one processed dataset with its metadata record, then
analysis-ready tables built from it, correctly and with the mistakes the checks must catch.
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "validation"))

import check_promotion_readiness as promotion  # noqa: E402
import validate_analysis_ready as ar  # noqa: E402

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone(timedelta(hours=-5)))
DATASET = "test_1_spending_annual"


def processed_rows() -> pd.DataFrame:
    """Two categories; 'New' is only published from 2021, and 'Old' has one value withheld as too unreliable."""
    rows = [("2019-01-01", "Old", "v1", 10.0, ""), ("2021-01-01", "Old", "v1", np.nan, "F"),
            ("2021-01-01", "New", "v2", 5.0, ""), ("2023-01-01", "Old", "v1", 12.0, ""), ("2023-01-01", "New", "v2", 6.0, "")]
    return pd.DataFrame([{"date": d, "geography": "Canada", "series": s, "value": v, "unit": "Dollars", "source": "test",
                          "dataset_id": DATASET, "status": st, "status_category": "unreliable" if st else "",
                          "geo_code": "g1", "series_id": i} for d, s, i, v, st in rows])


def hub(tmp_path) -> Path:
    path = tmp_path / "processed" / "test" / DATASET / f"{DATASET}.csv"
    path.parent.mkdir(parents=True)
    processed_rows().to_csv(path, index=False, na_rep="")
    (tmp_path / "metadata").mkdir()
    record = {"dataset_id": DATASET, "checksum": "sha256:abc", "processed_file_location": path.relative_to(tmp_path).as_posix(),
              "validation": {"status": "passed", "checks": []}}
    (tmp_path / "metadata" / f"{DATASET}.json").write_text(json.dumps(record), encoding="utf-8")
    return tmp_path


def table_record(**overrides) -> dict:
    record = {"table_id": "t", "inputs": [{"dataset_id": DATASET, "raw_checksum": "sha256:abc"}],
              "key": ["date", "series_id"], "derivations": {}}
    record.update(overrides)
    return record


def results(table, record, root):
    return {r["check"]: r for r in ar.validate_table(table, record, root, NOW)}


def test_a_selection_of_published_rows_passes(tmp_path):
    root = hub(tmp_path)
    checks = results(processed_rows(), table_record(), root)
    assert checks["provenance"]["status"] == "pass"
    assert checks["nulls_explained"]["status"] == "info"  # the empty value carries its status
    assert {r["status"] for r in checks.values()} <= {"pass", "info"}


def test_rows_created_by_a_pivot_and_melt_fail_provenance(tmp_path):
    root = hub(tmp_path)
    # The Week 4 mistake: pivot to wide and melt back. 'New' gains a 2019 row StatCan never published,
    # and the status column is lost.
    wide = processed_rows().pivot_table(index="date", columns="series_id", values="value", aggfunc="first")
    melted = wide.reset_index().melt(id_vars="date", var_name="series_id", value_name="value")
    labels = processed_rows().drop_duplicates("series_id").set_index("series_id")["series"]
    table = melted.assign(geography="Canada", series=melted["series_id"].map(labels), unit="Dollars", status="",
                          source="test", dataset_id=DATASET)
    checks = results(table, table_record(), root)
    assert checks["provenance"]["status"] == "fail" and checks["provenance"]["rows_without_source"] == 1
    assert checks["nulls_explained"]["status"] == "warn"


def test_a_changed_value_and_a_repeated_key_fail(tmp_path):
    root = hub(tmp_path)
    changed = processed_rows()
    changed.loc[0, "value"] = 99.0
    assert results(changed, table_record(), root)["provenance"]["status"] == "fail"
    repeated = pd.concat([processed_rows(), processed_rows().head(1)])
    checks = results(repeated, table_record(), root)
    assert checks["table_key"]["status"] == "fail" and checks["duplicate_rows"]["status"] == "warn"


def test_dropping_the_status_column_fails_the_layout(tmp_path):
    root = hub(tmp_path)
    assert results(processed_rows().drop(columns="status"), table_record(), root)["table_layout"]["status"] == "fail"


def test_derived_values_are_recomputed_from_the_source(tmp_path):
    root = hub(tmp_path)
    source = processed_rows()
    old = source[source["series_id"] == "v1"].dropna(subset=["value"])
    derived = old.assign(series="Old, monthly average (derived)", derivation="monthly_mean", status="", status_category="")
    record = table_record(key=["date", "series_id", "derivation"], derivations={"monthly_mean": "mean by month"})
    assert results(derived, record, root)["provenance"]["status"] == "pass"
    wrong = derived.assign(value=derived["value"] + 1)
    assert results(wrong, record, root)["provenance"]["status"] == "fail"
    undocumented = results(derived, table_record(key=["date", "series_id", "derivation"], derivations={"other": "x"}), root)
    assert undocumented["derivations_documented"]["status"] == "warn"


def test_a_table_built_from_older_data_is_flagged(tmp_path):
    root = hub(tmp_path)
    stale = table_record(inputs=[{"dataset_id": DATASET, "raw_checksum": "sha256:older"}])
    assert results(processed_rows(), stale, root)["inputs_current"]["status"] == "warn"


# ----------------------------------------------------------------------------
# Promotion workflow
# ----------------------------------------------------------------------------

def dataset_record(warn=True, rating="Confirmed High") -> dict:
    checks = [{"check": "metadata_completeness", "status": "pass", "detail": "ok"}]
    if warn:
        checks.append({"check": "freshness", "status": "warn", "detail": "old"})
    return {"dataset_id": DATASET, "checksum": "sha256:abc", "licence": {"name": "Open"},
            "reproducibility_rating": {"rating": rating},
            "validation": {"status": "passed_with_warnings" if warn else "passed", "checks": checks}}


def log_row(**fields) -> dict:
    row = {name: "" for name in promotion.LOG_FIELDS}
    row.update(dataset_id=DATASET, raw_checksum_reviewed="sha256:abc", preparer="Preparer")
    row.update(fields)
    return row


def test_a_warning_without_a_disposition_holds_the_dataset():
    result = promotion.assess_dataset(dataset_record(), None)
    assert result["decision"] == "held" and any("freshness" in line for line in result["open"])


def test_dispositions_make_it_ready_and_a_different_reviewer_promotes_it():
    proposed = log_row(dispositions="freshness: Accept - survey is biennial", decision="held")
    assert promotion.assess_dataset(dataset_record(), proposed)["decision"] == "ready for sign-off"
    self_signed = dict(proposed, reviewer="Preparer", decision="promoted")
    assert promotion.assess_dataset(dataset_record(), self_signed)["decision"] == "ready for sign-off"
    signed = dict(proposed, reviewer="Reviewer", decision="promoted", review_date="2026-11-08")
    assert promotion.assess_dataset(dataset_record(), signed)["decision"] == "promoted"


def test_a_sign_off_lapses_when_the_data_changes():
    signed = log_row(dispositions="freshness: Accept - ok", reviewer="Reviewer", decision="promoted")
    changed = dict(dataset_record(), checksum="sha256:new")
    result = promotion.assess_dataset(changed, signed)
    assert result["decision"] == "held" and any("review again" in line for line in result["open"])


def test_a_failed_validation_blocks_promotion():
    failed = dataset_record()
    failed["validation"]["status"] = "failed"
    assert promotion.assess_dataset(failed, log_row(reviewer="R", decision="promoted"))["decision"] == "held"


def test_qa_log_rows_follow_the_standard_layout(tmp_path):
    rows = promotion.qa_log_rows(DATASET, dataset_record()["validation"],
                                 log_row(dispositions="freshness: Accept - ok", decision="held"))
    assert list(rows[0]) == promotion.QA_LOG_FIELDS
    freshness = [r for r in rows if r["check_performed"] == "freshness"][0]
    assert (freshness["check_id"], freshness["severity"], freshness["status"]) == ("QA-TS3", "WARNING", "In review")
    assert freshness["corrective_action_or_note"] == "Accept: ok"


def test_the_promotion_log_keeps_the_latest_row_per_dataset(tmp_path):
    path = tmp_path / "promotion_log.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=promotion.LOG_FIELDS)
        writer.writeheader()
        writer.writerow(log_row(decision="held"))
        writer.writerow(log_row(decision="promoted", reviewer="R"))
    assert promotion.read_log(path)[DATASET]["decision"] == "promoted"
