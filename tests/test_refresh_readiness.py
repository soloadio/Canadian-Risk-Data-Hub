"""Offline tests for scripts/validation/check_refresh_readiness.py."""
from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "validation"))
sys.path.insert(0, str(ROOT / "tests"))

import check_refresh_readiness as readiness  # noqa: E402
from test_boc_acquisition import NOW, POLICY, YIELDS_ROW, FakeValet, run  # noqa: E402


def acquired(tmp_path, rows=(POLICY,)):
    assert run(tmp_path, FakeValet(), rows=list(rows)) == 0
    return json.loads((tmp_path / "metadata" / "boc_v39079_policy_rate_daily.json").read_text(encoding="utf-8"))


def check(tmp_path, capsys, now=NOW):
    code = readiness.main(["--repo-root", str(tmp_path)], now=now)
    return code, capsys.readouterr().out


def test_freshly_acquired_hub_is_ready(tmp_path, capsys):
    acquired(tmp_path)
    code, out = check(tmp_path, capsys)
    assert code == 0 and "OK" in out and "0 problem(s)" in out


def test_edited_raw_file_is_a_problem(tmp_path, capsys):
    meta = acquired(tmp_path)
    (tmp_path / meta["raw_file_location"]).write_bytes(b'{"edited": true}')
    code, out = check(tmp_path, capsys)
    assert code == 1 and "no longer matches its recorded checksum" in out


def test_missing_processed_file_is_a_problem(tmp_path, capsys):
    meta = acquired(tmp_path)
    (tmp_path / meta["processed_file_location"]).unlink()
    code, out = check(tmp_path, capsys)
    assert code == 1 and "processed file missing" in out


def test_catalogue_entry_never_acquired_is_a_problem(tmp_path, capsys):
    acquired(tmp_path)
    catalogue = tmp_path / "sources" / "boc_series.csv"
    catalogue.write_text(catalogue.read_text(encoding="utf-8") + YIELDS_ROW + "\n", encoding="utf-8")
    code, out = check(tmp_path, capsys)
    assert code == 1 and "no metadata record yet" in out and "BD.CDN.2YR.DQ.YLD" in out


def test_record_left_out_of_the_catalogue_and_old_retrieval_are_notices(tmp_path, capsys):
    acquired(tmp_path)
    (tmp_path / "sources" / "boc_series.csv").write_text("series_ids,id_part,subject,name,category,update_frequency\n",
                                                        encoding="utf-8")
    code, out = check(tmp_path, capsys, now=NOW + timedelta(days=10))
    assert code == 0
    assert "not in any source catalogue" in out and "due for a refresh" in out
