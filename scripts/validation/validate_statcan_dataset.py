#!/usr/bin/env python3
"""Quality checks for Statistics Canada datasets in the data hub.

The acquisition script (scripts/acquisition/statcan/download_statcan_table.py) runs these checks
on every download and records the results in the metadata record under "validation".

HOW TO USE ON ITS OWN
    Re-check a processed file that is already on disk, without downloading anything:
        python scripts/validation/validate_statcan_dataset.py statcan_11100222_household_spending_annual
    Check every dataset that has a metadata record and a processed file:
        python scripts/validation/validate_statcan_dataset.py --all

Each check returns PASS, WARN or FAIL. FAIL means the processed file should not be trusted;
the acquisition script refuses to overwrite the processed file and metadata when a check fails.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

PASS, WARN, FAIL = "pass", "warn", "fail"

# Statistics Canada STATUS codes (from the WDS getCodeSets service, plus "x" for suppression
# under the Statistics Act). These mean the value is intentionally absent.
MISSING_STATUS = {
    "..": "not available for the reference period",
    "...": "not applicable",
    "x": "suppressed to meet confidentiality requirements",
    "F": "too unreliable to be published",
}
# These accompany a published value.
VALUE_STATUS = {
    "A": "data quality: excellent",
    "B": "data quality: very good",
    "C": "data quality: good",
    "D": "data quality: acceptable",
    "E": "use with caution",
    "0s": "value rounded to zero",
    "<LOD": "less than the limit of detection",
}

PROCESSED_COLUMNS = ["date", "geography", "series", "value", "unit", "source", "dataset_id", "status"]
SERIES_KEY = ["geography", "series"]
OBSERVATION_KEY = ["date", "geography", "series"]
EXAMPLES = 5  # how many examples to keep in a check's detail


def check(name: str, status: str, detail: str, **data) -> dict:
    result = {"check": name, "status": status, "detail": detail}
    result.update(data)
    return result


# ----------------------------------------------------------------------------
# Checks on the raw table
# ----------------------------------------------------------------------------

def schema_of(raw: pd.DataFrame, dimension_columns: list[str]) -> dict:
    """The structural description of a raw table, used to detect schema changes between retrievals."""
    return {
        "columns": list(raw.columns),
        "dimension_columns": list(dimension_columns),
        "units": sorted(set(raw["UOM"])),
    }


def check_schema(current: dict, previous: dict | None, previous_version: int | None) -> tuple[dict, int]:
    """Compare this retrieval's schema with the one on record. Returns (result, schema_version).

    The version goes up by one when columns or dimensions change. A change in units alone is reported
    but does not change the version, because the layout of the processed file stays the same.
    """
    if not previous:
        return check("schema", PASS, "no earlier schema on record; this retrieval is the baseline"), 1
    version = previous_version or 1
    changes = []
    for key, label in [("columns", "columns"), ("dimension_columns", "dimensions")]:
        old, new = previous.get(key, []), current[key]
        added, removed = [c for c in new if c not in old], [c for c in old if c not in new]
        if added:
            changes.append(f"{label} added: {added}")
        if removed:
            changes.append(f"{label} removed: {removed}")
        if not added and not removed and old != new:
            changes.append(f"{label} reordered")
    unit_changes = []
    old_units, new_units = set(previous.get("units", [])), set(current["units"])
    if old_units != new_units:
        unit_changes.append(f"units added: {sorted(new_units - old_units)}, removed: {sorted(old_units - new_units)}")
    if changes:
        version += 1
        return check("schema", WARN, f"schema changed (now version {version}): " + "; ".join(changes + unit_changes)
                     + ". Check that downstream notebooks still find the series they use."), version
    if unit_changes:
        return check("schema", WARN, "layout unchanged, but " + "; ".join(unit_changes)), version
    return check("schema", PASS, f"same columns and dimensions as the previous retrieval (version {version})"), version


def check_row_count(rows_raw: int, expected_rows: int | None) -> dict:
    """Compare the rows received with the number of data points Statistics Canada says the table has."""
    if not expected_rows:
        return check("row_count", WARN, f"{rows_raw} rows received; the expected count was not available from the service")
    if rows_raw != expected_rows:
        return check("row_count", WARN, f"{rows_raw} rows received but Statistics Canada reports {expected_rows} data points; "
                     "the download may be incomplete or the table was updated during the download")
    return check("row_count", PASS, f"{rows_raw} rows received, matching the {expected_rows} data points Statistics Canada reports")


def select_geographies(raw: pd.DataFrame, requested: str) -> tuple[list[str], dict]:
    """Resolve the requested geography (a name, a DGUID, several separated by ';', or 'all').

    Matching ignores case and surrounding spaces. Unknown names are an error rather than a silent
    fallback, so a typo in the catalogue cannot produce a file with the wrong coverage.
    Returns (GEO values to keep, check result).
    """
    available = list(dict.fromkeys(raw["GEO"]))
    problems = []
    # Each GEO name should have one DGUID, otherwise two places share a name.
    if "DGUID" in raw.columns:
        pairs = raw[["GEO", "DGUID"]].drop_duplicates()
        ambiguous = sorted(pairs.loc[pairs["GEO"].duplicated(keep=False), "GEO"].unique())
        if ambiguous:
            problems.append(f"names used by more than one DGUID: {ambiguous[:EXAMPLES]}")

    if requested.strip().lower() == "all":
        status = WARN if problems else PASS
        return available, check("geography", status, f"all {len(available)} geographies kept"
                                + (f"; {problems[0]}" if problems else ""), requested="all", kept=len(available))

    by_name = {name.strip().lower(): name for name in available}
    by_dguid = {}
    if "DGUID" in raw.columns:
        by_dguid = {d.strip().lower(): g for g, d in raw[["GEO", "DGUID"]].drop_duplicates().itertuples(index=False)}
    wanted = [part.strip() for part in requested.split(";") if part.strip()]
    kept, unknown = [], []
    for name in wanted:
        match = by_name.get(name.lower()) or by_dguid.get(name.lower())
        if match:
            kept.append(match)
        else:
            unknown.append(name)
    if unknown or not kept:
        sample = ", ".join(available[:15]) + (", ..." if len(available) > 15 else "")
        return [], check("geography", FAIL, f"geography {unknown or wanted} not found in this table. "
                         f"Available ({len(available)}): {sample}", requested=requested)
    kept = list(dict.fromkeys(kept))
    status = WARN if problems else PASS
    return kept, check("geography", status, f"kept {kept} out of {len(available)} geographies in the source"
                       + (f"; {problems[0]}" if problems else ""), requested=requested, kept=len(kept))


def check_dates(unparsed: list[str]) -> dict:
    if unparsed:
        return check("dates", WARN, f"{len(unparsed)} reference periods in an unrecognized format were kept as-is: "
                     f"{unparsed[:EXAMPLES]}")
    return check("dates", PASS, "every reference period converted to an ISO date")


def check_numeric(non_numeric: list[str]) -> dict:
    if non_numeric:
        return check("numeric_values", FAIL, f"{len(non_numeric)} VALUE entries are not numbers, e.g. {non_numeric[:EXAMPLES]}; "
                     "the table layout may have changed")
    return check("numeric_values", PASS, "every non-empty VALUE is a number")


def check_terminated(raw_subset: pd.DataFrame) -> dict:
    if "TERMINATED" not in raw_subset.columns:
        return check("terminated_series", PASS, "no TERMINATED column in this table")
    terminated = raw_subset["TERMINATED"].astype(str).str.strip().str.lower().eq("t")
    if "VECTOR" in raw_subset.columns:
        count = int(raw_subset.loc[terminated, "VECTOR"].nunique())
    else:
        count = int(terminated.sum())
    if count:
        return check("terminated_series", PASS, f"{count} series are marked terminated by Statistics Canada "
                     "(no longer updated); they are kept with their history", terminated_series=count)
    return check("terminated_series", PASS, "no terminated series", terminated_series=0)


# ----------------------------------------------------------------------------
# Checks on the processed table
# ----------------------------------------------------------------------------

def remove_duplicates(processed: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Drop exact duplicate rows, and fail if two rows share date, geography and series with different values."""
    exact = processed.duplicated(keep="first")
    exact_count = int(exact.sum())
    deduplicated = processed.loc[~exact]
    key_dup = deduplicated.duplicated(OBSERVATION_KEY, keep=False)
    if key_dup.any():
        examples = deduplicated.loc[key_dup, OBSERVATION_KEY].drop_duplicates().head(EXAMPLES).to_dict("records")
        return deduplicated, check("duplicates", FAIL, f"{int(key_dup.sum())} rows share a date, geography and series but "
                                   f"have different values, e.g. {examples}. The series columns may not identify a series "
                                   "uniquely.", exact_duplicates_removed=exact_count, conflicting_rows=int(key_dup.sum()))
    if exact_count:
        return deduplicated, check("duplicates", WARN, f"{exact_count} exact duplicate rows removed",
                                   exact_duplicates_removed=exact_count, conflicting_rows=0)
    return deduplicated, check("duplicates", PASS, "no duplicate observations", exact_duplicates_removed=0, conflicting_rows=0)


def check_missing(processed: pd.DataFrame) -> dict:
    """Explain every missing value with a Statistics Canada status, and flag values that contradict their status."""
    status = processed["status"].fillna("").astype(str).str.strip()
    missing = processed["value"].isna()
    by_status = {(code or "(no status)"): int(n) for code, n in status[missing].value_counts().items()}
    unexplained = int((missing & ~status.isin(MISSING_STATUS)).sum())
    contradicting = int((~missing & status.isin(MISSING_STATUS)).sum())
    total = int(missing.sum())
    share = total / len(processed) if len(processed) else 0.0
    described = ", ".join(f"{n} {code} ({MISSING_STATUS.get(code, 'no explanation')})" for code, n in by_status.items()) or "none"
    detail = f"{total} of {len(processed)} observations have no value ({share:.1%}): {described}"
    problems = []
    if unexplained:
        problems.append(f"{unexplained} missing values have no status explaining why")
    if contradicting:
        problems.append(f"{contradicting} values are present although their status says they should be absent")
    return check("missing_values", WARN if problems else PASS, detail + ("; " + "; ".join(problems) if problems else ""),
                 missing=total, missing_by_status=by_status, unexplained=unexplained, contradicting=contradicting)


def period_index(dates: pd.Series, frequency: str) -> pd.Series | None:
    """Turn ISO dates into consecutive integers for the given frequency (None if gaps cannot be checked)."""
    parsed = pd.to_datetime(dates, format="%Y-%m-%d", errors="coerce")
    year, month = parsed.dt.year, parsed.dt.month
    if frequency == "annual":
        return year
    if frequency == "semiannual":
        return year * 2 + (month - 1) // 6
    if frequency == "quarterly":
        return year * 4 + (month - 1) // 3
    if frequency == "monthly":
        return year * 12 + (month - 1)
    return None


def period_label(index: int, frequency: str) -> str:
    if frequency == "annual":
        return f"{index}"
    if frequency == "semiannual":
        return f"{index // 2}-H{index % 2 + 1}"
    if frequency == "quarterly":
        return f"{index // 4}-Q{index % 4 + 1}"
    return f"{index // 12}-{index % 12 + 1:02d}"


def check_date_gaps(processed: pd.DataFrame, frequency: str) -> dict:
    """Look for reference periods missing from the table as a whole and from individual series.

    A period missing from the whole table usually reflects the publication schedule (for example a
    survey that moved to every second year). A period missing from one series only means a row is
    absent, which Statistics Canada normally avoids by publishing the row with a '..' status.
    """
    index = period_index(processed["date"], frequency)
    if index is None:
        return check("date_gaps", PASS, f"not checked for {frequency} data")
    frame = pd.DataFrame({"period": index, "geography": processed["geography"], "series": processed["series"]}).dropna(subset=["period"])
    if frame.empty:
        return check("date_gaps", WARN, "no recognizable dates to check")
    frame["period"] = frame["period"].astype(int)

    periods = sorted(frame["period"].unique())
    table_missing = sorted(set(range(periods[0], periods[-1] + 1)) - set(periods))

    spans = frame.groupby(SERIES_KEY, sort=False)["period"].agg(["min", "max", "nunique"])
    spans["missing"] = spans["max"] - spans["min"] + 1 - spans["nunique"]
    # Periods absent from the whole table are not counted again against each series.
    if table_missing:
        missing_array = pd.Series(table_missing)
        spans["missing"] -= spans.apply(lambda r: int(missing_array.between(r["min"], r["max"]).sum()), axis=1)
    series_with_gaps = spans[spans["missing"] > 0]

    details = []
    if table_missing:
        details.append(f"{len(table_missing)} {frequency} periods have no data in the whole table: "
                       f"{[period_label(p, frequency) for p in table_missing[:10]]}"
                       + (" ..." if len(table_missing) > 10 else ""))
    if len(series_with_gaps):
        examples = [" / ".join(map(str, key)) for key in series_with_gaps.index[:EXAMPLES]]
        details.append(f"{len(series_with_gaps)} series skip periods inside their own date range "
                       f"({int(series_with_gaps['missing'].sum())} periods in total), e.g. {examples}")
    if not details:
        return check("date_gaps", PASS, f"no gaps in the {len(periods)} {frequency} periods from "
                     f"{period_label(periods[0], frequency)} to {period_label(periods[-1], frequency)}",
                     table_missing_periods=[], series_with_gaps=0)
    return check("date_gaps", WARN, "; ".join(details),
                 table_missing_periods=[period_label(p, frequency) for p in table_missing],
                 series_with_gaps=int(len(series_with_gaps)))


def check_processed_layout(processed: pd.DataFrame) -> dict:
    missing = [c for c in PROCESSED_COLUMNS if c not in processed.columns]
    if missing or list(processed.columns) != PROCESSED_COLUMNS:
        return check("processed_layout", FAIL, f"expected columns {PROCESSED_COLUMNS}, found {list(processed.columns)}")
    if processed.empty:
        return check("processed_layout", FAIL, "the processed table has no rows")
    return check("processed_layout", PASS, f"{len(processed)} rows with the standard columns")


def check_processed(processed: pd.DataFrame, frequency: str) -> tuple[pd.DataFrame, list[dict]]:
    """All checks that only need the processed table. Returns the de-duplicated table and the results."""
    results = [check_processed_layout(processed)]
    if results[0]["status"] == FAIL:
        return processed, results
    processed, duplicates = remove_duplicates(processed)
    results += [duplicates, check_missing(processed), check_date_gaps(processed, frequency)]
    return processed, results


# ----------------------------------------------------------------------------
# Summaries
# ----------------------------------------------------------------------------

def overall_status(results: list[dict]) -> str:
    statuses = {r["status"] for r in results}
    if FAIL in statuses:
        return "failed"
    return "passed_with_warnings" if WARN in statuses else "passed"


def failures(results: list[dict]) -> list[str]:
    return [f"{r['check']}: {r['detail']}" for r in results if r["status"] == FAIL]


def warnings_of(results: list[dict]) -> list[str]:
    return [f"{r['check']}: {r['detail']}" for r in results if r["status"] == WARN]


# ----------------------------------------------------------------------------
# Command line: re-check processed files already on disk
# ----------------------------------------------------------------------------

def read_processed(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")
    if "value" in frame.columns:
        frame["value"] = pd.to_numeric(frame["value"].replace("", None), errors="coerce")
    return frame


def validate_dataset(metadata_path: Path, root: Path) -> tuple[str, list[dict]]:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    processed_path = root / metadata["processed_file_location"]
    if not processed_path.exists():
        return "skipped", [check("processed_file", WARN, f"{processed_path} not found; run the acquisition script first")]
    _, results = check_processed(read_processed(processed_path), metadata.get("frequency", ""))
    return overall_status(results), results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Re-run the quality checks on processed Statistics Canada files.")
    parser.add_argument("dataset_id", nargs="*", help="Dataset ID(s), e.g. statcan_11100222_household_spending_annual")
    parser.add_argument("--all", action="store_true", help="Check every statcan_* metadata record")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2],
                        help="Hub root folder (default: two levels above this script).")
    args = parser.parse_args(argv)

    metadata_dir = args.repo_root / "metadata"
    if args.all:
        paths = sorted(metadata_dir.glob("statcan_*.json"))
    else:
        paths = [metadata_dir / f"{d}.json" for d in args.dataset_id]
    if not paths:
        parser.error("give at least one dataset ID or --all")

    failed = 0
    for path in paths:
        print(f"\n{path.stem}")
        if not path.exists():
            print(f"  FAILED: no metadata record at {path}")
            failed += 1
            continue
        status, results = validate_dataset(path, args.repo_root)
        for r in results:
            print(f"  {r['status'].upper():4}  {r['check']}: {r['detail']}")
        print(f"  => {status}")
        failed += status == "failed"
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
