#!/usr/bin/env python3
"""Build the hub's analysis-ready tables from processed data, with a record and validation for each.

HOW TO USE
    From the main repository folder, after the acquisition scripts have run:
        python scripts/processing/build_analysis_ready.py
    Build some tables only:
        python scripts/processing/build_analysis_ready.py --only household_spending_by_category_annual

WHAT YOU GET (for each table)
    analysis_ready/<table>.csv             the table
    metadata/analysis_ready/<table>.json   its record: description, inputs (with the raw checksum each was built
                                           from), selection, derivation rules, key, columns, known limitations and
                                           the validation results (scripts/validation/validate_analysis_ready.py)

HOW THE TABLES ARE BUILT
    Every table is a selection of rows from processed files, kept in long format with the processed columns
    (date, geography, geo_code, series, series_id, value, unit, status, status_category, source, dataset_id),
    plus a readable column where useful (category, age_group). Series are chosen by their stable series_id,
    not by label. Nothing is reshaped to wide and back, so no row appears that the publisher did not publish.
    Derived values (monthly averages of daily series) are marked in a derivation column, labelled 'derived'
    in the series name, and their rule is written in the record and re-checked by the validator.

A table is still written when a check gives a warning; a failed check stops that table and leaves the
previous version in place.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

import pandas as pd

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "validation"))
import hub_checks as checks  # noqa: E402
import validate_analysis_ready as ar  # noqa: E402

BUILDER = "scripts/processing/build_analysis_ready.py"
STANDARD_COLUMNS = ["date", "geography", "geo_code", "series", "series_id", "value", "unit", "status",
                    "status_category", "source", "dataset_id"]

HOUSEHOLD = "statcan_11100222_household_spending_annual"
LFS = "statcan_14100287_labour_force_characteristics_monthly"
POLICY = "boc_v39079_policy_rate_daily"
CORRA = "boc_avgintwo_corra_daily"
YIELDS = "boc_bdcdnyld_bond_yields_daily"
FX = "boc_fxusdcad_exchange_rate_daily"

# Labour Force Survey vectors (Statistics Canada table 14-10-0287-01): unemployment rate, both genders,
# estimate, seasonally adjusted, Canada. Vectors identify the series even if StatCan rewords its labels.
UNEMPLOYMENT_BY_AGE = {
    "v2062815": "all_ages",        # 15 years and over
    "v2062842": "youth_15_24",     # 15 to 24 years
    "v2062950": "core_age_25_54",  # 25 to 54 years
    "v2062977": "older_55_plus",   # 55 years and over
}
UNEMPLOYMENT_ALL_AGES = "v2062815"


@dataclass
class Table:
    name: str
    description: str
    inputs: list[str]
    selection: str
    build: Callable[[dict[str, pd.DataFrame]], pd.DataFrame]
    key: list[str]
    known_limitations: str
    derivations: dict[str, str] = field(default_factory=dict)


def standard(frame: pd.DataFrame, extra: dict[str, pd.Series] | None = None, after: str = "geo_code") -> pd.DataFrame:
    """The standard columns in order, with any extra readable columns inserted after `after`."""
    out = frame[STANDARD_COLUMNS].copy()
    for position, (name, values) in enumerate((extra or {}).items()):
        out.insert(out.columns.get_loc(after) + 1 + position, name, values)
    return out.reset_index(drop=True)


# ----------------------------------------------------------------------------
# The tables
# ----------------------------------------------------------------------------

def build_household(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = data[HOUSEHOLD]
    rows = rows[(rows["geography"] == "Canada") & rows["series"].str.startswith("Average expenditure per household | ")]
    rows = rows.sort_values(["series", "date"])
    return standard(rows, {"category": rows["series"].str.split(" | ", regex=False).str[-1]})


def build_unemployment(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = data[LFS]
    rows = rows[rows["series_id"].isin(UNEMPLOYMENT_BY_AGE) & (rows["date"] >= "2000-01-01")]
    missing = set(UNEMPLOYMENT_BY_AGE) - set(rows["series_id"])
    if missing:
        raise ValueError(f"vectors {sorted(missing)} are not in {LFS}")
    rows = rows.assign(_order=rows["series_id"].map(list(UNEMPLOYMENT_BY_AGE).index)).sort_values(["_order", "date"])
    return standard(rows, {"age_group": rows["series_id"].map(UNEMPLOYMENT_BY_AGE)})


def build_rates_daily(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = pd.concat([data[d] for d in [POLICY, CORRA, YIELDS, FX]], ignore_index=True)
    return standard(rows.sort_values(["dataset_id", "series_id", "date"]))


def monthly_mean(rows: pd.DataFrame) -> pd.DataFrame:
    """Mean of the published values in each calendar month, dated the first day of the month (rule 'monthly_mean')."""
    out = []
    for series_id, series in rows.dropna(subset=["value"]).groupby("series_id"):
        first = series.iloc[0]
        means = series.set_index(pd.to_datetime(series["date"]))["value"].resample("MS").mean().dropna()
        out.append(pd.DataFrame({
            "date": means.index.strftime("%Y-%m-%d"), "geography": first["geography"], "geo_code": first["geo_code"],
            "series": f"{first['series']}, monthly average (derived)", "series_id": series_id,
            "derivation": "monthly_mean", "value": means.round(6).values, "unit": first["unit"], "status": "",
            "status_category": "", "source": first["source"], "dataset_id": first["dataset_id"],
        }))
    return pd.concat(out, ignore_index=True)


def build_rates_and_unemployment(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    policy, fx = data[POLICY], data[FX]
    ten_year = data[YIELDS][data[YIELDS]["series_id"] == "BD.CDN.10YR.DQ.YLD"]
    derived = monthly_mean(pd.concat([policy, ten_year, fx], ignore_index=True))
    unemployment = data[LFS][data[LFS]["series_id"] == UNEMPLOYMENT_ALL_AGES].assign(derivation="")
    combined = pd.concat([derived, unemployment[derived.columns]], ignore_index=True)
    # Keep the months every series covers, so the table lines up for comparison.
    coverage = combined.dropna(subset=["value"]).groupby("date")["series_id"].nunique()
    months = coverage[coverage == combined["series_id"].nunique()].index
    combined = combined[combined["date"].isin(months)].sort_values(["dataset_id", "series_id", "date"])
    return combined.reset_index(drop=True)


TABLES = [
    Table(
        name="household_spending_by_category_annual",
        description="Average annual spending per household in Canada by spending category, as published in the "
                    "Survey of Household Spending, one row per category and survey year.",
        inputs=[HOUSEHOLD],
        selection="Canada; statistic 'Average expenditure per household'; every published category and year, "
                  "including empty values with their status (F, ..).",
        build=build_household,
        key=["date", "series_id"],
        known_limitations="Survey years only (biennial since 2017: no 2018, 2020 or 2022). Categories changed between "
                          "2017 and 2019, so some cover only part of the period; years a category was not published "
                          "have no row. Categories are hierarchical (totals and components): do not sum them. "
                          "Current dollars. Empty values keep StatCan's reason in status (F = too unreliable to "
                          "publish, .. = not available).",
    ),
    Table(
        name="unemployment_rate_by_age_monthly",
        description="Monthly unemployment rate in Canada for all ages and three age groups, from the Labour Force "
                    "Survey, seasonally adjusted, both genders, from January 2000.",
        inputs=[LFS],
        selection="Vectors " + ", ".join(f"{v} ({g})" for v, g in UNEMPLOYMENT_BY_AGE.items())
                  + " (unemployment rate, Total - Gender, Estimate, Seasonally adjusted); dates from 2000-01-01.",
        build=build_unemployment,
        key=["date", "series_id"],
        known_limitations="Seasonally adjusted: StatCan revises these estimates each January for the previous three "
                          "years. Rates are in percent (points).",
    ),
    Table(
        name="policy_rate_corra_yields_usd_cad_daily",
        description="Business-daily Bank of Canada policy rate, CORRA, benchmark Government of Canada bond yields "
                    "(2, 5, 10-year and long-term) and the USD/CAD exchange rate, as published.",
        inputs=[POLICY, CORRA, YIELDS, FX],
        selection="Every published observation of the four Bank of Canada datasets.",
        build=build_rates_daily,
        key=["date", "series_id"],
        known_limitations="Market series have no row on market holidays; the policy rate is published every business "
                          "day. Benchmark yields can jump when the Bank moves a benchmark to a new bond. USD/CAD is "
                          "an indicative rate for statistical and analytical use.",
    ),
    Table(
        name="rates_and_unemployment_monthly",
        description="Monthly averages of the Bank of Canada policy rate, the 10-year benchmark bond yield and USD/CAD, "
                    "with the Statistics Canada unemployment rate (15 years and over, seasonally adjusted), for the "
                    "months all four cover. The first table combining two publishers.",
        inputs=[POLICY, YIELDS, FX, LFS],
        selection="Policy rate (V39079), 10-year yield (BD.CDN.10YR.DQ.YLD) and USD/CAD (FXUSDCAD) averaged by month; "
                  f"unemployment rate vector {UNEMPLOYMENT_ALL_AGES} as published; months covered by all four.",
        build=build_rates_and_unemployment,
        key=["date", "series_id", "derivation"],
        derivations={"monthly_mean": ar.DERIVATIONS["monthly_mean"]
                     + ". Applied to the Bank of Canada series; the unemployment rate is already monthly and is copied."},
        known_limitations="The monthly averages are derived by the hub, not published by the Bank; they average the "
                          "business days the Bank published in each month. Units differ by series (percent, Canadian "
                          "dollars per US dollar): compare movements, not levels.",
    ),
]


# ----------------------------------------------------------------------------
# Building and writing
# ----------------------------------------------------------------------------

def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def build_table(table: Table, root: Path, now: datetime) -> dict:
    """Build, validate and write one table. Returns a result dict; raises ValueError on a problem."""
    inputs = ar.load_inputs(root, table.inputs)
    absent = [d for d, (record, processed) in inputs.items() if not record or processed is None]
    if absent:
        raise ValueError(f"inputs not available (run the acquisition scripts first): {absent}")
    frame = table.build({d: processed for d, (_, processed) in inputs.items()})

    record = {
        "table_id": table.name,
        "file": f"analysis_ready/{table.name}.csv",
        "description": table.description,
        "built_by": BUILDER,
        "built_at": now.isoformat(timespec="seconds"),
        "inputs": [{"dataset_id": d, "raw_checksum": inputs[d][0].get("checksum"),
                    "last_retrieved": inputs[d][0].get("last_retrieved"),
                    "validation_status": (inputs[d][0].get("validation") or {}).get("status")} for d in table.inputs],
        "selection": table.selection,
        "derivations": table.derivations,
        "key": table.key,
        "columns": list(frame.columns),
        "rows": int(len(frame)),
        "observation_start": frame["date"].min(),
        "observation_end": frame["date"].max(),
        "known_limitations": table.known_limitations,
        "content_checksum": None,
        "validation": None,
    }
    results = ar.validate_table(frame, record, root, now)
    status = checks.overall_status(results)
    if status == "failed":
        raise ValueError("validation failed, previous table left in place:\n    " + "\n    ".join(checks.failures(results)))

    # "\n" line endings on every platform (pandas would use "\r\n" on Windows), so the content checksum is the
    # same on Windows and Linux.
    csv_bytes = frame.to_csv(index=False, na_rep="", lineterminator="\n").encode("utf-8")
    table_path = root / record["file"]
    atomic_write(table_path, csv_bytes)
    record["content_checksum"] = ar.file_checksum(table_path)
    record["validation"] = {"status": status, "run_at": now.isoformat(timespec="seconds"), "validator": ar.VALIDATOR,
                            "standard": "RAI_Metadata_QA_Standard_v1.0", "checks": results}
    record_path = root / "metadata" / "analysis_ready" / f"{table.name}.json"
    atomic_write(record_path, (json.dumps(record, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    return {"rows": len(frame), "status": status, "warnings": checks.warnings_of(results), "notes": checks.notes_of(results)}


def main(argv=None, now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the analysis-ready tables from processed data.")
    parser.add_argument("--only", action="append", metavar="TABLE", help="Build only this table (repeatable).")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2],
                        help="Hub root folder (default: two levels above this script).")
    args = parser.parse_args(argv)
    now = now or datetime.now().astimezone()

    tables = TABLES
    if args.only:
        unknown = set(args.only) - {t.name for t in TABLES}
        if unknown:
            parser.error(f"unknown tables {sorted(unknown)}; available: {[t.name for t in TABLES]}")
        tables = [t for t in TABLES if t.name in args.only]

    print(f"Building analysis-ready tables, {now.isoformat(timespec='seconds')}")
    failures = 0
    for table in tables:
        print(f"\n{table.name}")
        try:
            result = build_table(table, args.repo_root, now)
        except ValueError as exc:
            failures += 1
            print(f"  FAILED: {exc}")
            continue
        print(f"  SUCCESS: {result['rows']} rows, validation {result['status'].replace('_', ' ')}")
        for line in result["warnings"]:
            print(f"  warning: {line}")
        for line in result["notes"]:
            print(f"  note: {line}")
    print(f"\nDone: {len(tables) - failures} built, {failures} failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
