"""Quality checks shared by every publisher in the data hub.

These checks only look at the hub's own layouts (the processed table and the schema record), so
they work the same way for Statistics Canada, the Bank of Canada and any publisher added later.
Checks that depend on how one publisher lays out its raw files live next to that publisher's
code (for example validate_statcan_dataset.py).

Each check returns a dict: {"check": name, "status": "pass" | "info" | "warn" | "fail", "detail": text, ...}.
The statuses follow the team's metadata and QA standard (RAI_Metadata_QA_Standard_v1.0, Ayesha Malhi):
pass = PASS, info = INFO (a non-blocking note), warn = WARNING, fail = FAIL.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

PASS, INFO, WARN, FAIL = "pass", "info", "warn", "fail"

# The processed layout. The first eight columns are the Week 1 layout; the last three were added in Week 5:
#   status_category  the publisher's status symbol translated into the team's standard categories
#   geo_code         the publisher's official geography code (StatCan DGUID), blank if it has none
#   series_id        the publisher's stable series identifier (StatCan vector, Bank of Canada series name)
PROCESSED_COLUMNS = ["date", "geography", "series", "value", "unit", "source", "dataset_id", "status",
                     "status_category", "geo_code", "series_id"]
SERIES_KEY = ["geography", "series"]
OBSERVATION_KEY = ["date", "geography", "series"]
# The key the metadata record declares: the stable ID instead of the label (check_series_identity makes the
# two keys equivalent, so the duplicates check on OBSERVATION_KEY enforces this one too).
PRIMARY_KEY = ["date", "geography", "series_id"]
EXAMPLES = 5  # how many examples to keep in a check's detail

# Publisher status symbols and their standard category (RAI standard v1.0, "Status symbols").
# The publisher's own symbol stays in the status column; the category is added beside it.
STATUS_CATEGORIES = {
    "..": "not_available",
    "...": "not_applicable",
    "x": "suppressed",
    "F": "unreliable",
    "A": "quality_flag", "B": "quality_flag", "C": "quality_flag", "D": "quality_flag", "E": "quality_flag",
    "0s": "rounded_zero",
    "<LOD": "below_detection",
}
# Categories that mean "no value was published".
ABSENT_CATEGORIES = {"not_available", "not_applicable", "suppressed", "unreliable"}


def status_category(status: pd.Series) -> pd.Series:
    """Standard category for each status symbol: blank when there is no symbol, 'unknown' for a symbol not in the standard."""
    cleaned = status.fillna("").astype(str).str.strip()
    return cleaned.map(lambda s: "" if s == "" else STATUS_CATEGORIES.get(s, "unknown"))

# Daily, weekly and biweekly data: a series that goes longer than this without an observation has a gap
# (a week covers weekends and the longest run of Canadian statutory holidays).
MAX_GAP_DAYS = {"daily": 7, "weekly": 15, "biweekly": 29}
# How old the latest observation may be, in days, before the data counts as stale: one period plus
# the usual publication lag (e.g. monthly data appears about 5-6 weeks after the month starts).
MAX_AGE_DAYS = {"daily": 7, "weekly": 21, "biweekly": 35, "monthly": 75, "quarterly": 200,
                "semiannual": 380, "annual": 913}


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
    # Missing values that all carry a status are a non-blocking note (INFO in the team standard).
    status_level = WARN if problems else (INFO if total else PASS)
    return check("missing_values", status_level, detail + ("; " + "; ".join(problems) if problems else ""),
                 missing=total, missing_by_status=by_status, unexplained=unexplained, contradicting=contradicting)


def check_series_identity(processed: pd.DataFrame) -> dict:
    """Each series_id has one label and each label one series_id, within a geography.

    This is what lets joins use the stable ID while people read the label. A missing series_id is a
    warning (the publisher gives none, or the adapter does not fill it); a clash is a failure, because
    a join on either key would then mix different series.
    """
    if "series_id" not in processed.columns:
        return check("series_identity", WARN, "no series_id column; joins must rely on series labels")
    ids = processed["series_id"].fillna("").astype(str).str.strip()
    blank = int(ids.eq("").sum())
    pairs = processed.assign(series_id=ids)[[*SERIES_KEY, "series_id"]].drop_duplicates()
    pairs = pairs[pairs["series_id"] != ""]
    labels_per_id = pairs.groupby(["geography", "series_id"])["series"].nunique()
    ids_per_label = pairs.groupby(SERIES_KEY)["series_id"].nunique()
    clashes = []
    if (labels_per_id > 1).any():
        clashes.append(f"{int((labels_per_id > 1).sum())} series_id values have more than one label, "
                       f"e.g. {[k[1] for k in labels_per_id[labels_per_id > 1].index[:EXAMPLES]]}")
    if (ids_per_label > 1).any():
        clashes.append(f"{int((ids_per_label > 1).sum())} labels have more than one series_id, "
                       f"e.g. {[k[1] for k in ids_per_label[ids_per_label > 1].index[:EXAMPLES]]}")
    if clashes:
        return check("series_identity", FAIL, "; ".join(clashes), series=int(len(pairs)), rows_without_id=blank)
    if blank:
        return check("series_identity", WARN, f"{blank} rows have no series_id", series=int(len(pairs)), rows_without_id=blank)
    return check("series_identity", PASS, f"{len(pairs)} series, each with one stable series_id and one label",
                 series=int(len(pairs)), rows_without_id=0)


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
    if frequency in MAX_GAP_DAYS:
        return check_observation_spacing(processed, frequency)
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


def check_observation_spacing(processed: pd.DataFrame, frequency: str) -> dict:
    """Date gaps for daily, weekly and biweekly data, where calendars (weekends, holidays) make periods uneven.

    Instead of listing every missing period, look for stretches longer than MAX_GAP_DAYS without an
    observation inside each series' own date range.
    """
    limit = MAX_GAP_DAYS[frequency]
    dates = pd.to_datetime(processed["date"], format="%Y-%m-%d", errors="coerce")
    frame = pd.DataFrame({"date": dates, "geography": processed["geography"], "series": processed["series"]})
    frame = frame.dropna(subset=["date"]).drop_duplicates().sort_values([*SERIES_KEY, "date"])
    if frame.empty:
        return check("date_gaps", WARN, "no recognizable dates to check")
    frame["gap"] = frame.groupby(SERIES_KEY, sort=False)["date"].diff().dt.days
    gaps = frame[frame["gap"] > limit]
    if gaps.empty:
        return check("date_gaps", PASS, f"no series goes more than {limit} days without an observation "
                     f"({frame['date'].min():%Y-%m-%d} to {frame['date'].max():%Y-%m-%d})",
                     longest_gap_days=int(frame["gap"].max() or 0), series_with_gaps=0)
    worst = gaps.sort_values("gap", ascending=False).head(EXAMPLES)
    examples = [f"{r.series}: {int(r.gap)} days to {r.date:%Y-%m-%d}" for r in worst.itertuples()]
    affected = gaps[SERIES_KEY].drop_duplicates()
    return check("date_gaps", WARN, f"{len(gaps)} stretches of more than {limit} days without an observation in "
                 f"{len(affected)} series, e.g. {examples}", longest_gap_days=int(gaps["gap"].max()),
                 series_with_gaps=int(len(affected)))


def check_freshness(observation_end: str | None, frequency: str, now: datetime) -> dict:
    """Is the latest observation as recent as the frequency and the usual publication lag allow?

    A warning means either the publisher has not released newer data (e.g. a survey that moved to
    every second year) or the source stopped updating; either way the dataset should be looked at.
    """
    limit = MAX_AGE_DAYS.get(frequency)
    if limit is None or not observation_end:
        return check("freshness", PASS, f"not checked for {frequency or 'unknown'} data")
    latest = pd.to_datetime(observation_end, format="%Y-%m-%d", errors="coerce")
    if pd.isna(latest):
        return check("freshness", WARN, f"latest observation {observation_end!r} is not an ISO date")
    age = (pd.Timestamp(now.date()) - latest).days
    if age > limit:
        return check("freshness", WARN, f"latest observation is {observation_end}, {age} days old; {frequency} data "
                     f"is normally no more than {limit} days old. Check whether the source still updates",
                     age_days=age, limit_days=limit)
    return check("freshness", PASS, f"latest observation is {observation_end} ({age} day{'' if age == 1 else 's'} old, "
                 f"limit {limit})",
                 age_days=age, limit_days=limit)


def check_revisions(previous: pd.DataFrame | None, processed: pd.DataFrame) -> dict:
    """Compare with the previous processed file: values that changed and observations that disappeared.

    Publishers revise data, so a change is a note (INFO in the team standard, QA-RV1), not a failure:
    the point is that no earlier value is lost silently. The previous raw file is still stored, and
    this check says what moved. Observations are matched on series_id when both files have it, so a
    series the publisher relabels is reported as relabelled, not as removed and re-added.
    """
    if previous is None or previous.empty:
        return check("revisions", PASS, "no previous processed file to compare with")
    if any(c not in previous.columns for c in [*OBSERVATION_KEY, "value"]):
        return check("revisions", WARN, "the previous processed file does not have the standard columns; not compared")

    def has_ids(frame):
        return "series_id" in frame.columns and frame["series_id"].fillna("").astype(str).str.strip().ne("").all()

    key = PRIMARY_KEY if has_ids(previous) and has_ids(processed) else OBSERVATION_KEY
    as_text = {c: str for c in key}
    old = previous[[*dict.fromkeys([*key, "series"]), "value"]].astype(as_text | {"series": str})
    new = processed[[*dict.fromkeys([*key, "series"]), "value"]].astype(as_text | {"series": str})
    merged = old.merge(new, on=key, how="outer", suffixes=("_old", "_new"), indicator=True)
    both = merged[merged["_merge"] == "both"]
    a, b = both["value_old"], both["value_new"]
    changed = both[~((a.isna() & b.isna()) | ((a - b).abs() <= 1e-9 * (1 + a.abs())))]
    removed = merged[merged["_merge"] == "left_only"]
    added = int((merged["_merge"] == "right_only").sum())
    relabelled = []
    if key == PRIMARY_KEY:
        pairs = both[["series_id", "series_old", "series_new"]].drop_duplicates()
        relabelled = [f"{r.series_id}: {r.series_old!r} -> {r.series_new!r}"
                      for r in pairs[pairs["series_old"] != pairs["series_new"]].itertuples()]
    label = "series_old" if key == PRIMARY_KEY else "series"
    data = {"changed_values": int(len(changed)), "removed_observations": int(len(removed)), "new_observations": added,
            "relabelled_series": len(relabelled), "matched_on": key[-1]}
    if changed.empty and removed.empty and not relabelled:
        return check("revisions", PASS, f"no earlier value changed or disappeared; {added} new observations", **data)
    details = []
    if len(changed):
        examples = [f"{r.date} {getattr(r, label)}: {r.value_old} -> {r.value_new}" for r in changed.head(EXAMPLES).itertuples()]
        details.append(f"{len(changed)} values revised, e.g. {examples}")
    if len(removed):
        examples = [f"{r.date} {getattr(r, label)}" for r in removed.head(EXAMPLES).itertuples()]
        details.append(f"{len(removed)} observations no longer published, e.g. {examples}")
    if relabelled:
        details.append(f"{len(relabelled)} series relabelled by the publisher (same series_id), e.g. {relabelled[:EXAMPLES]}")
    return check("revisions", INFO, "; ".join(details) + f"; {added} new observations. "
                 "The previous version is kept in the earlier raw file", **data)


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
    results += [duplicates, check_series_identity(processed), check_missing(processed, missing_status),
                check_date_gaps(processed, frequency)]
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


def notes_of(results: list[dict]) -> list[str]:
    return [f"{r['check']}: {r['detail']}" for r in results if r["status"] == INFO]


def read_processed(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")
    if "value" in frame.columns:
        frame["value"] = pd.to_numeric(frame["value"].replace("", None), errors="coerce")
    return frame
