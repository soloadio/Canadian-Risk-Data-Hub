"""Quality checks shared by every publisher in the data hub.

These checks only look at the hub's own layouts (the processed table and the schema record), so
they work the same way for Statistics Canada, the Bank of Canada and any publisher added later.
Checks that depend on how one publisher lays out its raw files live next to that publisher's
code (for example validate_statcan_dataset.py).

Each check returns a dict: {"check": name, "status": "pass" | "warn" | "fail", "detail": text, ...}.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

PASS, WARN, FAIL = "pass", "warn", "fail"

PROCESSED_COLUMNS = ["date", "geography", "series", "value", "unit", "source", "dataset_id", "status"]
SERIES_KEY = ["geography", "series"]
OBSERVATION_KEY = ["date", "geography", "series"]
EXAMPLES = 5  # how many examples to keep in a check's detail


def check(name: str, status: str, detail: str, **data) -> dict:
    result = {"check": name, "status": status, "detail": detail}
    result.update(data)
    return result


# ----------------------------------------------------------------------------
# Checks on the raw table's structure
# ----------------------------------------------------------------------------

def schema_of(columns: list[str], dimension_columns: list[str], units) -> dict:
    """The structural description of a raw table, used to detect schema changes between retrievals."""
    return {
        "columns": list(columns),
        "dimension_columns": list(dimension_columns),
        "units": sorted(set(units)),
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


def check_missing(processed: pd.DataFrame, missing_status: dict[str, str] | None = None) -> dict:
    """Explain every missing value with a publisher status, and flag values that contradict their status.

    missing_status maps the publisher's status codes that mean "intentionally absent" to their meaning.
    A publisher without such codes (e.g. the Bank of Canada, which omits absent observations) passes {}.
    """
    missing_status = missing_status or {}
    status = processed["status"].fillna("").astype(str).str.strip()
    missing = processed["value"].isna()
    by_status = {(code or "(no status)"): int(n) for code, n in status[missing].value_counts().items()}
    unexplained = int((missing & ~status.isin(missing_status)).sum())
    contradicting = int((~missing & status.isin(missing_status)).sum())
    total = int(missing.sum())
    share = total / len(processed) if len(processed) else 0.0
    described = ", ".join(f"{n} {code} ({missing_status.get(code, 'no explanation')})" for code, n in by_status.items()) or "none"
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


def check_processed(processed: pd.DataFrame, frequency: str,
                    missing_status: dict[str, str] | None = None) -> tuple[pd.DataFrame, list[dict]]:
    """All checks that only need the processed table. Returns the de-duplicated table and the results."""
    results = [check_processed_layout(processed)]
    if results[0]["status"] == FAIL:
        return processed, results
    processed, duplicates = remove_duplicates(processed)
    results += [duplicates, check_missing(processed, missing_status), check_date_gaps(processed, frequency)]
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


def read_processed(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")
    if "value" in frame.columns:
        frame["value"] = pd.to_numeric(frame["value"].replace("", None), errors="coerce")
    return frame
