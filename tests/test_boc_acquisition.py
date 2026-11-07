"""Offline tests for the Bank of Canada acquisition script.

Run from the repository root:
    python -m pytest tests

The Valet API is replaced by a fake service, so no network access is needed.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "acquisition" / "boc"))
sys.path.insert(0, str(ROOT / "scripts" / "validation"))

import download_boc_series as boc  # noqa: E402
import hub_checks as checks  # noqa: E402

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone(timedelta(hours=-4)))
YIELDS = ["BD.CDN.2YR.DQ.YLD", "BD.CDN.10YR.DQ.YLD"]
LABELS = {"V39079": "Target for the overnight rate", "BD.CDN.2YR.DQ.YLD": "Benchmark bond yield, 2-year",
          "BD.CDN.5YR.DQ.YLD": "Benchmark bond yield, 5-year", "BD.CDN.10YR.DQ.YLD": "Benchmark bond yield, 10-year"}
DATES = ["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23"]


def observations(series_ids, dates=DATES, skip=()):
    """One observation per date, with every series present except the (date, series) pairs in skip."""
    rows = []
    for i, date in enumerate(dates):
        row = {"d": date}
        for j, sid in enumerate(series_ids):
            if (date, sid) not in skip:
                row[sid] = {"v": f"{2 + i / 100 + j:.4f}"}
        rows.append(row)
    return rows


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code, self._payload = status_code, payload
        self.content = json.dumps(payload).encode("utf-8") if payload is not None else b""

    def json(self):
        return self._payload


class FakeValet:
    """Answers GET /valet/observations/<ids>/json. obs maps a tuple of series IDs to its observations."""

    def __init__(self, obs=None, unknown=(), undescribed=(), unit="Percent"):
        self.obs, self.unknown, self.undescribed, self.unit = obs or {}, set(unknown), set(undescribed), unit
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        ids = url.split("/observations/")[1].split("/")[0].split(",")
        if self.unknown & set(ids):
            return FakeResponse(404, {"message": f"Series {sorted(self.unknown & set(ids))[0]} not found."})
        detail = {sid: {"label": LABELS.get(sid, sid), "description": f"About {sid}",
                        "dimension": {"key": "d", "name": "Date"},
                        "metadata": {"unit": self.unit, "frequency": "Business daily"}}
                  for sid in sorted(ids) if sid not in self.undescribed}
        return FakeResponse(payload={"terms": {"url": "https://www.bankofcanada.ca/terms/"}, "seriesDetail": detail,
                                     "observations": self.obs.get(tuple(ids), observations(ids))})


def write_catalogue(tmp_path, rows) -> Path:
    path = tmp_path / "sources" / "boc_series.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["series_ids,id_part,subject,name,category,update_frequency"] + rows
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


POLICY = "V39079,,policy_rate,,interest_rates,business daily"
YIELDS_ROW = f"{';'.join(YIELDS)},bdcdnyld,bond_yields,Benchmark yields,interest_rates,business daily"


def run(tmp_path, service, rows=(POLICY,), now=NOW):
    write_catalogue(tmp_path, list(rows))
    return boc.main(["--repo-root", str(tmp_path)], session=service, now=now)


def metadata(tmp_path, dataset_id="boc_v39079_policy_rate_daily"):
    return json.loads((tmp_path / "metadata" / f"{dataset_id}.json").read_text(encoding="utf-8"))


def by_check(meta):
    return {c["check"]: c for c in meta["validation"]["checks"]}


# ----------------------------------------------------------------------------
# End to end
# ----------------------------------------------------------------------------

def test_first_run_writes_raw_processed_and_full_metadata(tmp_path):
    service = FakeValet()
    assert run(tmp_path, service) == 0
    assert service.urls == ["https://www.bankofcanada.ca/valet/observations/V39079/json"]  # full history, no recent=n
    meta = metadata(tmp_path)
    for field in ["source_organization", "licence", "access", "retrieval_method", "schema_version", "schema",
                  "validation", "retrieval_history", "ai_use_note", "update_frequency"]:
        assert meta[field], field
    assert meta["source_organization"] == "Bank of Canada"
    assert meta["frequency"] == "daily" and meta["frequency_basis"] == "declared by Bank of Canada (Business daily)"
    assert meta["licence"]["url"] == "https://www.bankofcanada.ca/terms/"
    assert (meta["observation_start"], meta["observation_end"]) == ("2026-10-19", "2026-10-23")
    raw = tmp_path / meta["raw_file_location"]
    assert raw.suffix == ".json" and raw.read_bytes() == service.get(service.urls[0]).content  # stored as received
    processed = pd.read_csv(tmp_path / meta["processed_file_location"])
    assert list(processed.columns) == checks.PROCESSED_COLUMNS
    assert set(processed["source"]) == {"boc"} and len(processed) == len(DATES)


def test_holiday_rows_without_a_series_are_skipped_not_nulled(tmp_path):
    # 2026-10-21 is a market holiday: the 10-year series is absent from that observation.
    obs = observations(YIELDS, skip={("2026-10-21", "BD.CDN.10YR.DQ.YLD")})
    assert run(tmp_path, FakeValet({tuple(YIELDS): obs}), rows=[YIELDS_ROW]) == 0
    meta = metadata(tmp_path, "boc_bdcdnyld_bond_yields_daily")
    processed = pd.read_csv(tmp_path / meta["processed_file_location"])
    assert len(processed) == 2 * len(DATES) - 1
    assert processed["value"].notna().all()
    assert by_check(meta)["missing_values"]["missing"] == 0


def test_unchanged_data_reuses_the_raw_file_and_revised_data_adds_a_version(tmp_path):
    run(tmp_path, FakeValet())
    run(tmp_path, FakeValet())  # same day, same data: no new copy
    raw_dir = tmp_path / "raw" / "boc" / "boc_v39079_policy_rate_daily"
    assert len(list(raw_dir.glob("*.json"))) == 1

    revised = observations(["V39079"])
    revised[0]["V39079"]["v"] = "9.9900"
    run(tmp_path, FakeValet({("V39079",): revised}))
    assert sorted(p.name for p in raw_dir.glob("*.json")) == ["boc_v39079_policy_rate_daily_20261024.json",
                                                             "boc_v39079_policy_rate_daily_20261024_2.json"]
    assert [h["data_changed"] for h in metadata(tmp_path)["retrieval_history"]] == [None, False, True]


def test_unknown_series_fails_with_a_clear_message(tmp_path, capsys):
    assert run(tmp_path, FakeValet(unknown={"V39079"})) == 1
    assert "does not recognize this series" in capsys.readouterr().out
    assert not (tmp_path / "metadata" / "boc_v39079_policy_rate_daily.json").exists()


def test_series_missing_from_the_response_fails(tmp_path):
    assert run(tmp_path, FakeValet(undescribed={"BD.CDN.10YR.DQ.YLD"}), rows=[YIELDS_ROW]) == 1
    assert not (tmp_path / "metadata" / "boc_bdcdnyld_bond_yields_daily.json").exists()


def test_future_dates_fail_and_leave_previous_outputs(tmp_path):
    run(tmp_path, FakeValet())
    before = metadata(tmp_path)
    future = observations(["V39079"], dates=DATES + ["2027-01-04"])
    assert run(tmp_path, FakeValet({("V39079",): future}), now=NOW + timedelta(days=1)) == 1
    assert metadata(tmp_path) == before


def test_adding_a_series_to_a_dataset_raises_the_schema_version(tmp_path):
    run(tmp_path, FakeValet(), rows=[YIELDS_ROW])
    three = YIELDS_ROW.replace(";".join(YIELDS), ";".join(YIELDS + ["BD.CDN.5YR.DQ.YLD"]))
    assert run(tmp_path, FakeValet(), rows=[three], now=NOW + timedelta(days=1)) == 0
    meta = metadata(tmp_path, "boc_bdcdnyld_bond_yields_daily")
    assert meta["schema_version"] == 2
    assert by_check(meta)["schema"]["status"] == "warn" and "BD.CDN.5YR.DQ.YLD" in by_check(meta)["schema"]["detail"]


def test_dataset_id_stays_the_same_when_the_catalogue_subject_changes(tmp_path):
    run(tmp_path, FakeValet())
    assert run(tmp_path, FakeValet(), rows=[POLICY.replace("policy_rate", "overnight_target")],
               now=NOW + timedelta(days=1)) == 0
    assert sorted(p.stem for p in (tmp_path / "metadata").glob("*.json")) == ["boc_v39079_policy_rate_daily"]


def test_series_without_a_published_unit_warn(tmp_path):
    assert run(tmp_path, FakeValet(unit=""), rows=[YIELDS_ROW]) == 0
    assert by_check(metadata(tmp_path, "boc_bdcdnyld_bond_yields_daily"))["units"]["status"] == "warn"


def test_several_series_need_an_id_part(tmp_path, capsys):
    assert run(tmp_path, FakeValet(), rows=[YIELDS_ROW.replace("bdcdnyld", "")]) == 2
    assert "id_part is required" in capsys.readouterr().err


def test_processed_file_keeps_the_valet_series_name_as_series_id(tmp_path):
    assert run(tmp_path, FakeValet(), rows=[YIELDS_ROW]) == 0
    meta = metadata(tmp_path, "boc_bdcdnyld_bond_yields_daily")
    processed = pd.read_csv(tmp_path / meta["processed_file_location"], dtype=str, keep_default_na=False)
    assert set(processed["series_id"]) == set(YIELDS)
    assert set(processed["geo_code"]) == {""} and set(processed["status_category"]) == {""}
    assert by_check(meta)["series_identity"]["status"] == "pass"


def test_from_raw_rebuilds_from_the_stored_response(tmp_path):
    run(tmp_path, FakeValet())
    write_catalogue(tmp_path, [POLICY])
    assert boc.main(["--repo-root", str(tmp_path), "--from-raw"], session=None, now=NOW + timedelta(days=1)) == 0
    assert len(metadata(tmp_path)["retrieval_history"]) == 1
