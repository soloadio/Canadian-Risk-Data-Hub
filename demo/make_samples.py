#!/usr/bin/env python3
"""Cut the demo's sample files out of the real raw files (for maintainers; the demo itself does not need this).

HOW TO USE
    After a real acquisition run (so the raw files exist under raw/), from the repository root:
        python demo/make_samples.py
    then regenerate the expected outputs:
        python demo/run_demo.py --update-expected

WHAT IT KEEPS
    Every sample is a subset of a real raw file, with the publisher's own layout and values unchanged:
    - Statistics Canada 11-10-0222-01 (household spending): Canada, 'Average expenditure per household', eight
      categories chosen to show the cases the hub handles (a total, categories introduced or discontinued between
      survey years, values withheld as 'F' or '..'), every survey year.
    - Statistics Canada 14-10-0287-01 (Labour Force Survey): the four unemployment-rate vectors used by the
      analysis-ready table, from January 2024. The full table is 1.2 GB; this sample is a few kilobytes.
    - Bank of Canada Valet: the four datasets in the catalogue, observations from January 2026.
    For each StatCan table, a small JSON file stands in for the getCubeMetadata answer (title, release time,
    frequency code and the number of rows in the sample).
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "demo" / "sample"

HOUSEHOLD_CATEGORIES = [
    "Total expenditure", "Food expenditures", "Shelter", "Transportation",
    "Cannabis for non-medical use",                  # published only in later survey years
    "Convenience fees",                              # published only in later survey years
    "Collectors' items (e.g. stamps, coins)",        # skips a survey year
    "Charging expenses for electric vehicles",       # every Canada value withheld
]
LFS_VECTORS = ["v2062815", "v2062842", "v2062950", "v2062977"]
LFS_FROM = "2024-01"
BOC_FROM = "2026-01-01"
STATCAN_FREQUENCY_CODES = {"annual": 12, "monthly": 6}


def record(dataset_id: str) -> dict:
    return json.loads((ROOT / "metadata" / f"{dataset_id}.json").read_text(encoding="utf-8"))


def write_statcan(name: str, rows: pd.DataFrame, meta: dict) -> None:
    buffer = io.StringIO()
    rows.to_csv(buffer, index=False, quoting=csv.QUOTE_ALL, lineterminator="\n")
    (SAMPLE / f"{name}.csv").write_bytes(("﻿" + buffer.getvalue()).encode("utf-8"))  # StatCan files start with a BOM
    cube = {"cubeTitleEn": meta["dataset_name"], "releaseTime": meta["source_last_updated"][:16],
            "frequencyCode": STATCAN_FREQUENCY_CODES[meta["frequency"]], "nbDatapointsCube": len(rows)}
    (SAMPLE / f"{name}_cube.json").write_text(json.dumps(cube, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"{name}.csv: {len(rows)} rows")


def household() -> None:
    meta = record("statcan_11100222_household_spending_annual")
    raw = pd.read_csv(ROOT / meta["raw_file_location"], dtype=str, keep_default_na=False, encoding="utf-8-sig")
    category = raw.columns[raw.columns.get_loc("Statistic") + 1]
    keep = (raw["GEO"] == "Canada") & (raw["Statistic"] == "Average expenditure per household") \
        & raw[category].isin(HOUSEHOLD_CATEGORIES)
    write_statcan("statcan_11100222", raw[keep], meta)


def labour_force() -> None:
    meta = record("statcan_14100287_labour_force_characteristics_monthly")
    parts = []
    for chunk in pd.read_csv(ROOT / meta["raw_file_location"], dtype=str, keep_default_na=False,
                             encoding="utf-8-sig", chunksize=500_000):
        parts.append(chunk[chunk["VECTOR"].isin(LFS_VECTORS) & (chunk["REF_DATE"] >= LFS_FROM)])
    write_statcan("statcan_14100287", pd.concat(parts), meta)


def bank_of_canada() -> None:
    for path in sorted((ROOT / "metadata").glob("boc_*.json")):
        meta = json.loads(path.read_text(encoding="utf-8"))
        payload = json.loads((ROOT / meta["raw_file_location"]).read_text(encoding="utf-8"))
        payload["observations"] = [o for o in payload["observations"] if o.get("d", "") >= BOC_FROM]
        out = SAMPLE / f"boc_{meta['dataset_id'].split('_')[1]}.json"  # e.g. boc_bdcdnyld.json
        out.write_text(json.dumps(payload, indent=4, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        print(f"{out.name}: {len(payload['observations'])} observations")


if __name__ == "__main__":
    SAMPLE.mkdir(parents=True, exist_ok=True)
    household()
    labour_force()
    bank_of_canada()
