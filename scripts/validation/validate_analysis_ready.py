#!/usr/bin/env python3
"""Quality checks for analysis-ready tables (the last stage before analysis, dashboards and AI use).

HOW TO USE
    Re-check every analysis-ready table that has a record in metadata/analysis_ready/:
        python scripts/validation/validate_analysis_ready.py --all
    Re-check one:
        python scripts/validation/validate_analysis_ready.py household_spending_by_category_annual

scripts/processing/build_analysis_ready.py runs the same checks every time it builds a table and stores
the results in the table's record (metadata/analysis_ready/<table>.json). Nothing is changed here.

THE CHECKS
    table_layout            the standard columns are present (dataset_id, unit and status must be kept)  FAIL
    key_fields              no empty date, geography, series_id or dataset_id                            FAIL
    table_key               the declared key identifies one row                                          FAIL
    duplicate_rows          no repeated rows                                                             WARNING
    nulls_explained         every empty value carries the publisher's status saying why                 INFO / WARNING
    provenance              every row is a real published observation (same value and status as the
                            processed file it came from), and every derived value can be recomputed     FAIL
    derivations_documented  every derived row's rule is described in the table's record                WARNING
    inputs_current          the table was built from the data now on record, which passed validation    WARNING / FAIL
    unit_consistency        one unit per series                                                         FAIL
    future_dates            no date after today                                                         FAIL

The provenance check is what catches rows created by reshaping: a pivot to a wide table and a melt back
fills every year-and-category gap with an empty row the publisher never published.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hub_checks as checks  # noqa: E402
import rai_checks  # noqa: E402

REQUIRED_COLUMNS = ["date", "geography", "series", "series_id", "value", "unit", "status", "source", "dataset_id"]
SOURCE_KEY = ["dataset_id", "date", "geography", "series_id"]
VALIDATOR = "scripts/validation/validate_analysis_ready.py"

# Derivation rules the validator can recompute, by name. Each takes the input series (values by ISO date)
# and returns the derived values by ISO date.
DERIVATIONS = {
    "monthly_mean": "mean of every published value in the calendar month, dated the first day of the month",
}


def recompute(rule: str, source: pd.Series) -> pd.Series | None:
    if rule == "monthly_mean":
        dates = pd.to_datetime(source.index)
        means = source.groupby(dates.to_period("M")).mean()
        means.index = means.index.to_timestamp().strftime("%Y-%m-%d")
        return means
    return None


def file_checksum(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def read_table(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")
    if "value" in frame.columns:
        frame["value"] = pd.to_numeric(frame["value"].replace("", None), errors="coerce")
    return frame


def load_inputs(root: Path, dataset_ids) -> dict[str, tuple[dict, pd.DataFrame | None]]:
    """Each input's current metadata record and processed file (None where missing)."""
    inputs = {}
    for dataset_id in dataset_ids:
        record_path = root / "metadata" / f"{dataset_id}.json"
        record = json.loads(record_path.read_text(encoding="utf-8")) if record_path.exists() else {}
        processed_path = root / record["processed_file_location"] if record.get("processed_file_location") else None
        processed = checks.read_processed(processed_path) if processed_path and processed_path.exists() else None
        inputs[dataset_id] = (record, processed)
    return inputs


# ----------------------------------------------------------------------------
# Checks
# ----------------------------------------------------------------------------

def check_table_layout(table: pd.DataFrame, record: dict) -> dict:
    required = REQUIRED_COLUMNS + (["derivation"] if record.get("derivations") else [])
    missing = [c for c in required if c not in table.columns]
    if missing:
        return checks.check("table_layout", checks.FAIL, f"columns missing: {missing}. Analysis-ready tables keep "
                            "dataset_id, unit, status and series_id so every row can be traced and explained")
    if table.empty:
        return checks.check("table_layout", checks.FAIL, "the table has no rows")
    return checks.check("table_layout", checks.PASS, f"{len(table)} rows with the required columns")


def check_table_key(table: pd.DataFrame, key: list[str]) -> dict:
    missing = [c for c in key if c not in table.columns]
    if missing:
        return checks.check("table_key", checks.FAIL, f"key columns {missing} are not in the table")
    repeated = table.duplicated(key, keep=False)
    if repeated.any():
        examples = table.loc[repeated, key].drop_duplicates().head(checks.EXAMPLES).to_dict("records")
        return checks.check("table_key", checks.FAIL, f"{int(repeated.sum())} rows share a key {key}, e.g. {examples}",
                            key=key, repeated_rows=int(repeated.sum()))
    return checks.check("table_key", checks.PASS, f"{key} identifies every row", key=key, repeated_rows=0)


def check_duplicate_rows(table: pd.DataFrame) -> dict:
    repeated = int(table.duplicated().sum())
    if repeated:
        return checks.check("duplicate_rows", checks.WARN, f"{repeated} rows are exact copies of another row",
                            duplicate_rows=repeated)
    return checks.check("duplicate_rows", checks.PASS, "no repeated rows", duplicate_rows=0)


def check_nulls_explained(table: pd.DataFrame) -> dict:
    missing = table["value"].isna()
    status = table["status"].fillna("").astype(str).str.strip()
    unexplained = int((missing & status.eq("")).sum())
    total = int(missing.sum())
    by_status = {(s or "(no status)"): int(n) for s, n in status[missing].value_counts().items()}
    detail = f"{total} of {len(table)} values empty ({total / len(table):.1%}), by status: {by_status or 'none'}"
    if unexplained:
        return checks.check("nulls_explained", checks.WARN, detail + f"; {unexplained} have no status saying why",
                            missing=total, unexplained=unexplained, missing_by_status=by_status)
    return checks.check("nulls_explained", checks.INFO if total else checks.PASS, detail if total else "no empty values",
                        missing=total, unexplained=0, missing_by_status=by_status)


def check_provenance(table: pd.DataFrame, inputs: dict, record: dict) -> dict:
    """Every non-derived row exists in its source's processed file with the same value and status;
    every derived row matches its rule recomputed from the source."""
    derivation = table["derivation"].fillna("") if "derivation" in table.columns else pd.Series("", index=table.index)
    source_rows, derived_rows = table[derivation.eq("")], table[derivation.ne("")]
    problems, data = [], {"source_rows": int(len(source_rows)), "derived_rows": int(len(derived_rows))}

    # Rows copied from processed files.
    processed = [p for _, p in inputs.values() if p is not None]
    missing_inputs = [d for d, (_, p) in inputs.items() if p is None]
    if missing_inputs:
        problems.append(f"processed files not found for {missing_inputs}")
    if processed and len(source_rows):
        reference = pd.concat(processed, ignore_index=True)[[*SOURCE_KEY, "value", "status"]]
        merged = source_rows[[*SOURCE_KEY, "value", "status"]].merge(reference, on=SOURCE_KEY, how="left",
                                                                     suffixes=("", "_source"), indicator=True)
        invented = merged[merged["_merge"] == "left_only"]
        found = merged[merged["_merge"] == "both"]
        a, b = found["value"], found["value_source"]
        value_differs = found[~((a.isna() & b.isna()) | ((a - b).abs() <= 1e-9 * (1 + b.abs())))]
        status_differs = found[found["status"].fillna("") != found["status_source"].fillna("")]
        data.update(rows_without_source=int(len(invented)), values_differing=int(len(value_differs)),
                    statuses_differing=int(len(status_differs)))
        if len(invented):
            examples = invented[["dataset_id", "date", "series_id"]].head(checks.EXAMPLES).to_dict("records")
            problems.append(f"{len(invented)} rows have no published observation behind them (created during "
                            f"transformation), e.g. {examples}")
        if len(value_differs):
            problems.append(f"{len(value_differs)} values differ from the processed file")
        if len(status_differs):
            problems.append(f"{len(status_differs)} statuses differ from the processed file")

    # Derived rows, recomputed from the inputs.
    recomputed, unknown_rules = 0, set()
    groups = derived_rows.groupby(["dataset_id", "series_id", "derivation"]) if len(derived_rows) else []
    for (dataset_id, series_id, rule), rows in groups:
        processed_input = inputs.get(dataset_id, (None, None))[1]
        if processed_input is None:
            continue
        source = processed_input[processed_input["series_id"] == series_id].dropna(subset=["value"])
        expected = recompute(rule, source.set_index("date")["value"])
        if expected is None:
            unknown_rules.add(rule)
            continue
        got = rows.set_index("date")["value"]
        aligned = pd.concat({"got": got, "expected": expected.reindex(got.index)}, axis=1)
        wrong = aligned[~np.isclose(aligned["got"], aligned["expected"], rtol=0, atol=1e-6, equal_nan=False)]
        recomputed += len(aligned) - len(wrong)
        if len(wrong):
            problems.append(f"{len(wrong)} {rule} values of {series_id} do not match the recomputed values, "
                            f"e.g. {wrong.head(3).round(6).to_dict('index')}")
    data["derived_values_recomputed"] = recomputed
    if unknown_rules:
        problems.append(f"derivation rules the validator cannot recompute: {sorted(unknown_rules)}")
    if problems:
        return checks.check("provenance", checks.FAIL, "; ".join(problems), **data)
    detail = f"all {len(source_rows)} source rows match a published observation (value and status)"
    if len(derived_rows):
        detail += f"; all {recomputed} derived values match their rule recomputed from the source"
    return checks.check("provenance", checks.PASS, detail, **data)


def check_derivations_documented(table: pd.DataFrame, record: dict) -> dict:
    if "derivation" not in table.columns or table["derivation"].fillna("").eq("").all():
        return checks.check("derivations_documented", checks.PASS, "no derived rows")
    used = sorted(set(table["derivation"].fillna("")) - {""})
    documented = record.get("derivations") or {}
    undocumented = [r for r in used if r not in documented]
    derived = table[table["derivation"].fillna("") != ""]
    unlabelled = sorted(set(derived.loc[~derived["series"].str.contains("derived", case=False), "series"]))
    problems = []
    if undocumented:
        problems.append(f"rules used but not described in the record: {undocumented}")
    if unlabelled:
        problems.append(f"derived series not labelled as derived: {unlabelled[:checks.EXAMPLES]}")
    if problems:
        return checks.check("derivations_documented", checks.WARN, "; ".join(problems))
    return checks.check("derivations_documented", checks.PASS,
                        f"derived rows use documented rules {used} and are labelled as derived")


def check_inputs_current(record: dict, inputs: dict) -> dict:
    """The table was built from the raw data now on record, and every input passed its own validation."""
    stale, failed = [], []
    for entry in record.get("inputs", []):
        current, _ = inputs.get(entry["dataset_id"], ({}, None))
        if current.get("checksum") != entry.get("raw_checksum"):
            stale.append(entry["dataset_id"])
        if (current.get("validation") or {}).get("status") == "failed":
            failed.append(entry["dataset_id"])
    if failed:
        return checks.check("inputs_current", checks.FAIL, f"inputs whose latest validation failed: {failed}")
    if stale:
        return checks.check("inputs_current", checks.WARN, f"inputs changed since the table was built: {stale}; "
                            "rebuild it with scripts/processing/build_analysis_ready.py")
    return checks.check("inputs_current", checks.PASS,
                        f"built from the current data of all {len(record.get('inputs', []))} inputs, which passed validation")


def validate_table(table: pd.DataFrame, record: dict, root: Path, now: datetime) -> list[dict]:
    inputs = load_inputs(root, [i["dataset_id"] for i in record.get("inputs", [])])
    results = [check_table_layout(table, record)]
    if results[0]["status"] == checks.FAIL:
        return results
    key = record.get("key") or ["dataset_id", "date", "geography", "series_id"]
    results += [
        # derivation is part of some keys but is blank by design on published (non-derived) rows.
        rai_checks.check_key_fields(table, fields=[c for c in dict.fromkeys(["date", "geography", "series_id", "dataset_id", *key])
                                                   if c != "derivation"]),
        check_table_key(table, key),
        check_duplicate_rows(table),
        check_nulls_explained(table),
        check_provenance(table, inputs, record),
        check_derivations_documented(table, record),
        check_inputs_current(record, inputs),
        rai_checks.check_unit_consistency(table),
        rai_checks.check_future_dates(table, now),
    ]
    return results


# ----------------------------------------------------------------------------
# Command line
# ----------------------------------------------------------------------------

def main(argv=None, now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(description="Re-run the quality checks on analysis-ready tables (nothing is changed).")
    parser.add_argument("table", nargs="*", help="Table name(s), e.g. household_spending_by_category_annual")
    parser.add_argument("--all", action="store_true", help="Check every table with a record in metadata/analysis_ready/")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2],
                        help="Hub root folder (default: two levels above this script).")
    args = parser.parse_args(argv)
    now = now or datetime.now().astimezone()

    records_dir = args.repo_root / "metadata" / "analysis_ready"
    paths = sorted(records_dir.glob("*.json")) if args.all else [records_dir / f"{t}.json" for t in args.table]
    if not paths:
        parser.error("give at least one table name or --all")

    failed = 0
    for path in paths:
        print(f"\n{path.stem}")
        if not path.exists():
            print(f"  FAILED: no record at {path}")
            failed += 1
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        table_path = args.repo_root / record["file"]
        if not table_path.exists():
            print(f"  FAILED: table file {record['file']} not found")
            failed += 1
            continue
        results = validate_table(read_table(table_path), record, args.repo_root, now)
        for r in results:
            print(f"  {rai_checks.SEVERITY.get(r['status'], r['status']):7}  {r['check']}: {r['detail']}")
        status = checks.overall_status(results)
        print(f"  => {status}")
        failed += status == "failed"
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
