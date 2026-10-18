"""Offline tests for the Statistics Canada acquisition script and its quality checks.

Run from the repository root:
    python -m pytest tests

Statistics Canada is replaced by a fake service, so no network access is needed.
"""
from __future__ import annotations

import csv
import io
import json
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "acquisition" / "statcan"))
sys.path.insert(0, str(ROOT / "scripts" / "validation"))

import download_statcan_table as dst  # noqa: E402
import validate_statcan_dataset as checks  # noqa: E402

PID = "11100222"
HEADER = ["REF_DATE", "GEO", "DGUID", "Statistic", "Category", "UOM", "UOM_ID", "SCALAR_FACTOR", "SCALAR_ID",
          "VECTOR", "COORDINATE", "VALUE", "STATUS", "SYMBOL", "TERMINATED", "DECIMALS"]
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone(timedelta(hours=-4)))


def make_rows(years=(2015, 2016, 2017, 2019), geos=(("Canada", "2016A000011124"), ("Ontario", "2016A000235")),
              categories=("Total expenditure", "Food")):
    rows = []
    for year in years:
        for geo, dguid in geos:
            for i, category in enumerate(categories):
                rows.append({"REF_DATE": str(year), "GEO": geo, "DGUID": dguid, "Statistic": "Average expenditure",
                             "Category": category, "UOM": "Dollars", "UOM_ID": "81", "SCALAR_FACTOR": "units",
                             "SCALAR_ID": "0", "VECTOR": f"v{hash((geo, category)) % 10**6}", "COORDINATE": "1.1",
                             "VALUE": str(1000 + year + i), "STATUS": "", "SYMBOL": "", "TERMINATED": "",
                             "DECIMALS": "0"})
    return rows


def to_csv(rows, header=HEADER) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=header, quoting=csv.QUOTE_ALL, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return ("﻿" + buffer.getvalue()).encode("utf-8")


class FakeResponse:
    def __init__(self, status_code=200, payload=None, content=b""):
        self.status_code, self._payload, self._content = status_code, payload, content

    def json(self):
        return self._payload

    def iter_content(self, chunk_size):
        yield self._content


class FakeStatCan:
    """Answers the three WDS calls the script makes."""

    def __init__(self, data_csv: bytes, title="Household spending, Canada, regions and provinces",
                 frequency_code=12, datapoints=None):
        self.data_csv, self.title, self.frequency_code = data_csv, title, frequency_code
        self.datapoints = datapoints

    def get(self, url, **kwargs):
        if "getFullTableDownloadCSV" in url:
            return FakeResponse(payload={"status": "SUCCESS", "object": "https://example.test/table.zip"})
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr(f"{PID}.csv", self.data_csv)
            z.writestr(f"{PID}_MetaData.csv", b"Cube Title\nHousehold spending\n")
        return FakeResponse(content=archive.getvalue())

    def post(self, url, **kwargs):
        rows = self.datapoints if self.datapoints is not None else self.data_csv.count(b"\n") - 1
        return FakeResponse(payload=[{"status": "SUCCESS", "object": {
            "cubeTitleEn": self.title, "releaseTime": "2026-09-18T08:30", "frequencyCode": self.frequency_code,
            "nbDatapointsCube": rows}}])


def run(tmp_path, service, table="11-10-0222-01:household_spending:household", now=NOW, extra=()):
    return dst.main(["--repo-root", str(tmp_path), "--table", table, *extra], session=service, now=now)


def metadata(tmp_path, dataset_id="statcan_11100222_household_spending_annual"):
    return json.loads((tmp_path / "metadata" / f"{dataset_id}.json").read_text(encoding="utf-8"))


# ----------------------------------------------------------------------------
# End to end
# ----------------------------------------------------------------------------

def test_first_run_writes_raw_processed_and_full_metadata(tmp_path):
    assert run(tmp_path, FakeStatCan(to_csv(make_rows()))) == 0
    meta = metadata(tmp_path)
    for field in ["source_organization", "licence", "access", "retrieval_method", "schema_version", "schema",
                  "validation", "retrieval_history", "ai_use_note"]:
        assert meta[field], field
    assert meta["source_organization"] == "Statistics Canada"
    assert meta["schema_version"] == 1
    assert meta["frequency_basis"] == "declared by Statistics Canada"
    assert (tmp_path / meta["raw_file_location"]).exists()
    processed = pd.read_csv(tmp_path / meta["processed_file_location"])
    assert list(processed.columns) == checks.PROCESSED_COLUMNS
    assert set(processed["geography"]) == {"Canada"}


def test_dataset_id_stays_the_same_when_title_and_frequency_change(tmp_path):
    run(tmp_path, FakeStatCan(to_csv(make_rows())))
    # The title changes and the publisher now calls it "every 2 years" (not in the hub vocabulary),
    # and no subject is given, so the derived ID would differ.
    later = FakeStatCan(to_csv(make_rows(years=(2015, 2017, 2019, 2021))), title="Survey of household spending",
                        frequency_code=13)
    assert run(tmp_path, later, table="11-10-0222-01", now=NOW + timedelta(days=1)) == 0
    assert sorted(p.stem for p in (tmp_path / "metadata").glob("*.json")) == ["statcan_11100222_household_spending_annual"]
    assert metadata(tmp_path)["frequency"] == "annual"


def test_raw_files_are_versioned_never_overwritten(tmp_path):
    run(tmp_path, FakeStatCan(to_csv(make_rows())))
    run(tmp_path, FakeStatCan(to_csv(make_rows())))  # same day, same data: no new copy
    raw_dir = tmp_path / "raw" / "statcan" / "statcan_11100222_household_spending_annual"
    assert len(list(raw_dir.glob("*_2026101?.csv"))) == 1

    changed = make_rows()
    changed[0]["VALUE"] = "99999"
    run(tmp_path, FakeStatCan(to_csv(changed)))  # same day, new data: a second file, the first is untouched
    data_files = sorted(p.name for p in raw_dir.glob("*.csv") if "cube_metadata" not in p.name)
    assert data_files == ["statcan_11100222_household_spending_annual_20261018.csv",
                          "statcan_11100222_household_spending_annual_20261018_2.csv"]
    history = metadata(tmp_path)["retrieval_history"]
    assert [h["data_changed"] for h in history] == [None, False, True]
    assert history[1]["raw_file"] == history[0]["raw_file"]


def test_unknown_geography_fails_instead_of_falling_back(tmp_path, capsys):
    assert run(tmp_path, FakeStatCan(to_csv(make_rows())), extra=["--geography", "Ontari0"]) == 1
    out = capsys.readouterr().out
    assert "not found in this table" in out and "Ontario" in out
    assert not (tmp_path / "metadata" / "statcan_11100222_household_spending_annual.json").exists()


def test_geography_matching_ignores_case_and_accepts_several(tmp_path):
    assert run(tmp_path, FakeStatCan(to_csv(make_rows())), extra=["--geography", "canada; ONTARIO"]) == 0
    processed = pd.read_csv(tmp_path / metadata(tmp_path)["processed_file_location"])
    assert set(processed["geography"]) == {"Canada", "Ontario"}


def test_conflicting_duplicates_fail_and_leave_previous_outputs(tmp_path):
    run(tmp_path, FakeStatCan(to_csv(make_rows())))
    before = metadata(tmp_path)
    rows = make_rows()
    clash = dict(rows[0], VALUE="1")
    assert run(tmp_path, FakeStatCan(to_csv(rows + [clash])), now=NOW + timedelta(days=1)) == 1
    assert metadata(tmp_path) == before


def test_exact_duplicates_are_removed_with_a_warning(tmp_path):
    rows = make_rows()
    assert run(tmp_path, FakeStatCan(to_csv(rows + [rows[0]]))) == 0
    result = {c["check"]: c for c in metadata(tmp_path)["validation"]["checks"]}["duplicates"]
    assert result["status"] == "warn" and result["exact_duplicates_removed"] == 1


def test_schema_change_is_detected_and_versioned(tmp_path):
    run(tmp_path, FakeStatCan(to_csv(make_rows())))
    renamed = [dict(r, **{"Spending category": r["Category"]}) for r in make_rows()]
    header = [("Spending category" if c == "Category" else c) for c in HEADER]
    assert run(tmp_path, FakeStatCan(to_csv(renamed, header)), now=NOW + timedelta(days=1)) == 0
    meta = metadata(tmp_path)
    assert meta["schema_version"] == 2
    schema = {c["check"]: c for c in meta["validation"]["checks"]}["schema"]
    assert schema["status"] == "warn" and "Spending category" in schema["detail"]


def test_missing_values_are_explained_by_status(tmp_path):
    rows = make_rows()
    rows[0].update(VALUE="", STATUS="F")
    rows[1].update(VALUE="", STATUS="")  # both rows are Canada
    run(tmp_path, FakeStatCan(to_csv(rows)))
    result = {c["check"]: c for c in metadata(tmp_path)["validation"]["checks"]}["missing_values"]
    assert result["missing_by_status"] == {"F": 1, "(no status)": 1}
    assert result["unexplained"] == 1 and result["status"] == "warn"


def test_non_numeric_values_fail(tmp_path):
    rows = make_rows()
    rows[0]["VALUE"] = "1,234"
    assert run(tmp_path, FakeStatCan(to_csv(rows))) == 1


# ----------------------------------------------------------------------------
# Individual checks
# ----------------------------------------------------------------------------

def processed_frame(dates, series="s"):
    return pd.DataFrame({"date": dates, "geography": "Canada", "series": series, "value": 1.0, "unit": "u",
                         "source": "statcan", "dataset_id": "x", "status": ""})


def test_date_gaps_reports_periods_missing_from_the_table():
    result = checks.check_date_gaps(processed_frame(["2016-01-01", "2017-01-01", "2019-01-01"]), "annual")
    assert result["status"] == "warn" and result["table_missing_periods"] == ["2018"]


def test_date_gaps_reports_series_with_holes():
    frame = pd.concat([processed_frame(["2026-01-01", "2026-02-01", "2026-03-01"], "a"),
                       processed_frame(["2026-01-01", "2026-03-01"], "b")])
    result = checks.check_date_gaps(frame, "monthly")
    assert result["table_missing_periods"] == [] and result["series_with_gaps"] == 1


def test_date_gaps_pass_for_complete_quarterly_data():
    result = checks.check_date_gaps(processed_frame(["2025-10-01", "2026-01-01", "2026-04-01"]), "quarterly")
    assert result["status"] == "pass"


def test_row_count_warns_when_short():
    assert checks.check_row_count(10, 12)["status"] == "warn"
    assert checks.check_row_count(12, 12)["status"] == "pass"


def test_validator_command_line_rechecks_files_on_disk(tmp_path, capsys):
    run(tmp_path, FakeStatCan(to_csv(make_rows())))
    assert checks.main(["--all", "--repo-root", str(tmp_path)]) == 0
    assert "date_gaps" in capsys.readouterr().out
