#!/usr/bin/env python3
"""Quality checks for Statistics Canada datasets in the data hub.

The acquisition script (scripts/acquisition/statcan/download_statcan_table.py) runs these checks
on every download and records the results in the metadata record under "validation".

This module holds the checks that depend on Statistics Canada's raw layout (status codes, GEO and
DGUID columns, terminated series, the data-point count the service reports). The checks every
publisher shares live in hub_checks.py and are re-exported here, so existing imports keep working.

HOW TO USE ON ITS OWN
    Re-check a processed file that is already on disk, without downloading anything:
        python scripts/validation/validate_statcan_dataset.py statcan_11100222_household_spending_annual
    Check every Statistics Canada dataset that has a metadata record and a processed file:
        python scripts/validation/validate_statcan_dataset.py --all
    (scripts/validation/validate_dataset.py does the same for every publisher.)

Each check returns PASS, WARN or FAIL. FAIL means the processed file should not be trusted;
the acquisition script refuses to overwrite the processed file and metadata when a check fails.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hub_checks  # noqa: E402
from hub_checks import (  # noqa: E402,F401  (re-exported for the acquisition script and tests)
    EXAMPLES, FAIL, INFO, OBSERVATION_KEY, PASS, PROCESSED_COLUMNS, SERIES_KEY, WARN, check, check_date_gaps, check_dates,
    check_numeric, check_processed_layout, check_schema, failures, overall_status, period_index, period_label,
    read_processed, remove_duplicates, warnings_of, notes_of, status_category, STATUS_CATEGORIES,
)

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


# ----------------------------------------------------------------------------
# Checks on the raw Statistics Canada table
# ----------------------------------------------------------------------------

def schema_of(raw: pd.DataFrame, dimension_columns: list[str]) -> dict:
    """The structural description of a raw table, used to detect schema changes between retrievals."""
    return hub_checks.schema_of(list(raw.columns), dimension_columns, raw["UOM"])


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


def check_terminated(raw_subset: pd.DataFrame) -> dict:
    if "TERMINATED" not in raw_subset.columns:
        return check("terminated_series", PASS, "no TERMINATED column in this table")
    terminated = raw_subset["TERMINATED"].astype(str).str.strip().str.lower().eq("t")
    if "VECTOR" in raw_subset.columns:
        count = int(raw_subset.loc[terminated, "VECTOR"].nunique())
    else:
        count = int(terminated.sum())
    if count:
        return check("terminated_series", INFO, f"{count} series are marked terminated by Statistics Canada "
                     "(no longer updated); they are kept with their history", terminated_series=count)
    return check("terminated_series", PASS, "no terminated series", terminated_series=0)


# ----------------------------------------------------------------------------
# Checks on the processed table, with Statistics Canada's status codes
# ----------------------------------------------------------------------------

def check_missing(processed: pd.DataFrame) -> dict:
    return hub_checks.check_missing(processed, MISSING_STATUS)


def check_processed(processed: pd.DataFrame, frequency: str) -> tuple[pd.DataFrame, list[dict]]:
    return hub_checks.check_processed(processed, frequency, MISSING_STATUS)


# ----------------------------------------------------------------------------
# Command line: re-check processed files already on disk
# ----------------------------------------------------------------------------

def main(argv=None) -> int:
    import validate_dataset  # the shared command line, limited to Statistics Canada records
    return validate_dataset.main(argv, sources=["statcan"])


if __name__ == "__main__":
    sys.exit(main())
