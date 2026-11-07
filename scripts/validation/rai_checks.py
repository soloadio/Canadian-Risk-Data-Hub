"""RAI add-on quality checks, from the team's metadata and QA standard.

The standard (RAI_Metadata_QA_Standard_v1.0, Ayesha Malhi, W3-AYE-01/02) lists 30 checks. Thirteen
were already in the hub (hub_checks.py and the publisher adapters); this module adds the automated
ones the hub did not have, under the names the standard uses:

    key_fields             QA-CMP1  key fields never empty                                  FAIL
    missing_share          QA-CMP2  share of missing values within 5%                        INFO / WARNING
    value_range            QA-VAL1  values within a plausible range (percentages 0-100)      WARNING
    percent_convention     QA-VAL2  percentages stored as points (5.2), not decimals (0.052)  WARNING
    status_symbols         QA-VAL3  only status symbols the standard knows                   INFO / WARNING
    future_dates           QA-TS2   no observation dated after today                         FAIL
    period_change          QA-TS4   large period-over-period changes listed for review       INFO / WARNING
    unit_consistency       QA-XF2   one unit per series                                      FAIL
    metadata_completeness  QA-PRV1  every Required metadata key filled                       WARNING
    metadata_consistency   QA-PRV2  the table agrees with its metadata record                WARNING

and, for event tables (one row per incident, e.g. Ontario spills), which have no time series:
text_sentinels, constant_columns, category_variants, multi_valued (QA-CMP2 / QA-VAL3) and
zero_as_missing (QA-VAL1).

Ayesha's tests (tests/test_rai_checks.py) are the contract for this module. Her own version of the
module was not accessible when this one was written (Week 5); if the two differ, reconcile them and
keep the tests passing. Thresholds are starting defaults, to be calibrated per dataset.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from hub_checks import EXAMPLES, FAIL, INFO, PASS, STATUS_CATEGORIES, WARN, check

# The standard's 32 Required and 4 Recommended metadata keys (sheet 1_Dataset_Metadata).
REQUIRED_KEYS = [
    "dataset_id", "dataset_name", "description", "source_organization", "source_url", "source_table_or_series_id",
    "licence", "access", "access_format", "retrieval_method", "retrieval_script", "subject_category", "geography",
    "frequency", "frequency_basis", "observation_start", "observation_end", "units", "last_retrieved",
    "raw_file_location", "processed_file_location", "update_frequency", "checksum", "primary_key",
    "reproducibility_rating", "schema_version", "schema", "quality_notes", "known_limitations", "validation",
    "retrieval_history", "ai_use_note",
]
RECOMMENDED_KEYS = ["refresh_schedule", "revision_policy", "source_last_updated", "priority"]
REVISION_POLICIES = ["none", "scheduled", "ad_hoc", "unknown"]

# Each check's ID in the standard's 30-check list (sheet 4_QA_Checklist), for the QA log. Hub checks included.
CHECK_IDS = {
    # RAI add-on
    "key_fields": "QA-CMP1", "missing_share": "QA-CMP2", "text_sentinels": "QA-CMP2", "constant_columns": "QA-CMP2",
    "value_range": "QA-VAL1", "zero_as_missing": "QA-VAL1", "percent_convention": "QA-VAL2",
    "status_symbols": "QA-VAL3", "category_variants": "QA-VAL3", "multi_valued": "QA-VAL3",
    "future_dates": "QA-TS2", "period_change": "QA-TS4", "unit_consistency": "QA-XF2",
    "metadata_completeness": "QA-PRV1", "metadata_consistency": "QA-PRV2",
    # Already in the hub
    "row_count": "QA-ACQ2", "processed_layout": "QA-SCH1", "schema": "QA-SCH2", "dates": "QA-SCH3",
    "numeric_values": "QA-SCH3", "geography": "QA-CMP3", "series_present": "QA-CMP3", "duplicates": "QA-DUP1",
    "series_identity": "QA-DUP1", "missing_values": "QA-CMP2", "date_gaps": "QA-TS1", "freshness": "QA-TS3",
    "revisions": "QA-RV1", "terminated_series": "QA-CMP3", "units": "QA-XF2", "terms": "QA-PRV3",
    "empty_values": "QA-CMP2",
    # Analysis-ready tables (scripts/validation/validate_analysis_ready.py)
    "table_layout": "QA-SCH1", "table_key": "QA-DUP1", "duplicate_rows": "QA-DUP2", "nulls_explained": "QA-CMP2",
    "provenance": "QA-PRV2", "inputs_current": "QA-PRV2", "derivations_documented": "QA-PRV1",
}
SEVERITY = {PASS: "PASS", INFO: "INFO", WARN: "WARNING", FAIL: "FAIL"}

MISSING_SHARE_LIMIT = 0.05      # above this share of empty values: WARNING (5% default, standard finding 6)
PERIOD_CHANGE_SIGMAS = 8.0      # a change this many robust standard deviations from the typical change is flagged
PERIOD_CHANGE_WARN_SHARE = 0.01  # flagged changes above this share of all changes: WARNING, else INFO
TEXT_SENTINELS = {"unknown / n/a", "unknown", "n/a", "na", "not available", "none", "-", "--", ".."}
ZERO_SHARE_LIMIT = 0.10


def _series_groups(frame: pd.DataFrame) -> list[str]:
    """Columns that identify one series: dataset and series (series_id when the table has it)."""
    if "series_id" in frame.columns and frame["series_id"].fillna("").astype(str).str.strip().ne("").all():
        series = "series_id"
    else:
        series = "series"
    return [c for c in ["dataset_id", "geography", series] if c in frame.columns]


def _is_percent(unit: pd.Series) -> pd.Series:
    return unit.fillna("").astype(str).str.strip().str.lower().isin(["percent", "percentage", "%", "percentage points"])


# ----------------------------------------------------------------------------
# Time-series tables (processed and analysis-ready)
# ----------------------------------------------------------------------------

def check_key_fields(frame: pd.DataFrame, fields=("date", "geography", "series", "dataset_id")) -> dict:
    present = [f for f in fields if f in frame.columns]
    empty = {f: int(frame[f].fillna("").astype(str).str.strip().eq("").sum()) for f in present}
    empty = {f: n for f, n in empty.items() if n}
    if empty:
        return check("key_fields", FAIL, f"key fields with empty values: {empty}", empty=empty)
    return check("key_fields", PASS, f"no empty values in {present}")


def check_missing_share(frame: pd.DataFrame, limit: float = MISSING_SHARE_LIMIT) -> dict:
    total = len(frame)
    missing = int(frame["value"].isna().sum()) if total else 0
    share = missing / total if total else 0.0
    detail = f"{missing} of {total} values empty ({share:.1%}; limit {limit:.0%})"
    if not missing:
        return check("missing_share", PASS, detail, missing=0, share=0.0)
    status = WARN if share > limit else INFO
    note = ". Above the limit: a reviewer records a disposition (accept with reason, fix or waive)" if status == WARN else ""
    return check("missing_share", status, detail + note, missing=missing, share=round(share, 4))


def check_value_range(frame: pd.DataFrame, low: float = 0.0, high: float = 100.0) -> dict:
    """Percentages (unit 'Percent') should lie between 0 and 100; other units have no default range."""
    percent = _is_percent(frame["unit"]) & frame["value"].notna()
    outside = frame[percent & ((frame["value"] < low) | (frame["value"] > high))]
    if len(outside):
        examples = [f"{r.date} {r.series}: {r.value}" for r in outside.head(EXAMPLES).itertuples()]
        return check("value_range", WARN, f"{len(outside)} percentage values outside {low:g}-{high:g}, e.g. {examples}",
                     outside=int(len(outside)))
    return check("value_range", PASS, f"{int(percent.sum())} percentage values within {low:g}-{high:g}"
                 if percent.any() else "no unit with a default range")


def check_percent_convention(frame: pd.DataFrame) -> dict:
    """Percentages are stored as points (5.2 means 5.2%). A percentage series whose values all lie within
    -1 and 1 was probably stored as a decimal fraction.

    Standard errors are left out: they are legitimately small (the Labour Force Survey's standard error
    of the unemployment rate is about 0.1-0.2 percentage points), so they would always be flagged.
    """
    percent = frame[_is_percent(frame["unit"]) & frame["value"].notna()]
    if "series" in percent.columns:
        percent = percent[~percent["series"].astype(str).str.contains("standard error", case=False)]
    if percent.empty:
        return check("percent_convention", PASS, "no percentage values (standard errors excluded)")
    suspect = [key for key, g in percent.groupby(_series_groups(percent)) if g["value"].abs().max() <= 1.0 and len(g) >= 3]
    if suspect:
        return check("percent_convention", WARN, f"{len(suspect)} percentage series never exceed 1 in absolute value; "
                     f"check they are points, not decimals, e.g. {[k[-1] for k in suspect[:EXAMPLES]]}",
                     series_flagged=len(suspect))
    return check("percent_convention", PASS, "percentage series are stored as points")


def check_status_symbols(frame: pd.DataFrame, known: dict | None = None) -> dict:
    known = STATUS_CATEGORIES if known is None else known
    symbols = frame["status"].fillna("").astype(str).str.strip() if "status" in frame.columns else pd.Series(dtype=str)
    used = symbols[symbols != ""].value_counts()
    if used.empty:
        return check("status_symbols", PASS, "no status symbols")
    unknown = {s: int(n) for s, n in used.items() if s not in known}
    if unknown:
        return check("status_symbols", WARN, f"status symbols not in the standard: {unknown}", unknown=unknown)
    counts = {s: int(n) for s, n in used.items()}
    return check("status_symbols", INFO, f"status symbols present (all known): {counts}", symbols=counts)


def check_future_dates(frame: pd.DataFrame, now: datetime) -> dict:
    today = now.date().isoformat()
    dates = frame["date"].astype(str)
    future = sorted(d for d in dates.unique() if len(d) == 10 and d > today)
    if future:
        return check("future_dates", FAIL, f"{len(future)} observation dates are after {today}, e.g. {future[:EXAMPLES]}")
    return check("future_dates", PASS, f"no observation dated after {today}")


def check_period_change(frame: pd.DataFrame, sigmas: float = PERIOD_CHANGE_SIGMAS,
                        warn_share: float = PERIOD_CHANGE_WARN_SHARE) -> dict:
    """Flag period-over-period changes far outside each series' typical change (robust z-score on the changes).

    Flags are review items, not errors: real events (a policy-rate step, the 2020 unemployment jump) are
    flagged too. Changes are between consecutive observations, so a biennial survey compares values two
    years apart.
    """
    groups = _series_groups(frame)
    ordered = frame[frame["value"].notna()].sort_values([*groups, "date"])
    changes = ordered.groupby(groups, sort=False)["value"].diff()
    ordered = ordered.assign(change=changes).dropna(subset=["change"])
    if ordered.empty:
        return check("period_change", PASS, "fewer than two observations per series")
    flagged = []
    for _, g in ordered.groupby(groups, sort=False):
        median = g["change"].median()
        spread = 1.4826 * (g["change"] - median).abs().median()
        if spread == 0:
            spread = g["change"].abs().mean() or 0.0
        if spread == 0:
            continue
        flagged.append(g[(g["change"] - median).abs() > sigmas * spread])
    flagged = pd.concat(flagged) if flagged else ordered.iloc[0:0]
    share = len(flagged) / len(ordered)
    if flagged.empty:
        return check("period_change", PASS, f"no change beyond {sigmas:g} robust standard deviations in "
                     f"{len(ordered)} period-over-period changes", flagged=0, changes=int(len(ordered)))
    largest = flagged.reindex(flagged["change"].abs().sort_values(ascending=False).index).head(EXAMPLES)
    examples = [f"{r.date} {r.series}: {r.change:+.4g}" for r in largest.itertuples()]
    status = WARN if share > warn_share else INFO
    return check("period_change", status, f"{len(flagged)} of {len(ordered)} changes ({share:.2%}) are unusually large "
                 f"and listed for review (QA-OUT2), e.g. {examples}", flagged=int(len(flagged)),
                 changes=int(len(ordered)), share=round(share, 4))


def check_unit_consistency(frame: pd.DataFrame) -> dict:
    groups = _series_groups(frame)
    units = frame.groupby(groups)["unit"].nunique()
    mixed = units[units > 1]
    if len(mixed):
        return check("unit_consistency", FAIL, f"{len(mixed)} series have more than one unit, "
                     f"e.g. {[k[-1] if isinstance(k, tuple) else k for k in mixed.index[:EXAMPLES]]}",
                     series_with_mixed_units=int(len(mixed)))
    return check("unit_consistency", PASS, f"one unit per series ({len(units)} series)")


# ----------------------------------------------------------------------------
# Metadata
# ----------------------------------------------------------------------------

def _empty(value) -> bool:
    return value is None or (isinstance(value, (str, list, dict)) and len(value) == 0)


def check_metadata_completeness(metadata: dict, filled_later=()) -> dict:
    """Every Required key is filled, and the description says more than the dataset name.

    filled_later names keys the caller fills after running the checks (the acquisition scripts write
    'validation' and 'quality_notes' from these results), so they are not reported as missing.
    """
    missing = [k for k in REQUIRED_KEYS if k not in filled_later and _empty(metadata.get(k))]
    problems = []
    if missing:
        problems.append(f"Required keys empty or absent: {missing}")
    name, description = str(metadata.get("dataset_name") or "").strip(), str(metadata.get("description") or "").strip()
    if description and description == name:
        problems.append("description only repeats the dataset name")
    policy = metadata.get("revision_policy")
    if policy and policy not in REVISION_POLICIES:
        problems.append(f"revision_policy {policy!r} is not one of {REVISION_POLICIES}")
    recommended_missing = [k for k in RECOMMENDED_KEYS if _empty(metadata.get(k))]
    if problems:
        return check("metadata_completeness", WARN, "; ".join(problems), missing=missing,
                     recommended_missing=recommended_missing)
    if recommended_missing:
        return check("metadata_completeness", INFO, f"all {len(REQUIRED_KEYS)} Required keys filled; Recommended keys "
                     f"empty: {recommended_missing}", missing=[], recommended_missing=recommended_missing)
    return check("metadata_completeness", PASS, f"all {len(REQUIRED_KEYS)} Required and {len(RECOMMENDED_KEYS)} "
                 "Recommended keys filled", missing=[], recommended_missing=[])


def check_metadata_consistency(frame: pd.DataFrame, metadata: dict) -> dict:
    """The table agrees with its record: dataset_id, and observation_end equal to the last date with a value."""
    problems = []
    ids = set(frame["dataset_id"].astype(str)) if "dataset_id" in frame.columns else set()
    if metadata.get("dataset_id") and ids and ids != {metadata["dataset_id"]}:
        problems.append(f"dataset_id in the table {sorted(ids)} differs from the record's {metadata['dataset_id']!r}")
    dates = frame.loc[frame["value"].notna(), "date"].astype(str)
    if metadata.get("observation_end") and len(dates) and dates.max() != metadata["observation_end"]:
        problems.append(f"observation_end is {metadata['observation_end']} but the last date with a value is {dates.max()}")
    if metadata.get("observation_start") and len(dates) and dates.min() != metadata["observation_start"]:
        problems.append(f"observation_start is {metadata['observation_start']} but the first date with a value is {dates.min()}")
    if problems:
        return check("metadata_consistency", WARN, "; ".join(problems))
    return check("metadata_consistency", PASS, "dataset_id and observation dates agree with the metadata record")


# ----------------------------------------------------------------------------
# Event tables (one row per incident; not run on the hub's time series)
# ----------------------------------------------------------------------------

def check_text_sentinels(frame: pd.DataFrame, columns) -> dict:
    found = {}
    for c in columns:
        n = int(frame[c].astype(str).str.strip().str.lower().isin(TEXT_SENTINELS).sum())
        if n:
            found[c] = n
    if found:
        return check("text_sentinels", WARN, f"text used for missing values (convert to blank plus a status): {found}",
                     columns=found)
    return check("text_sentinels", PASS, "no text used for missing values")


def check_constant_columns(frame: pd.DataFrame, columns) -> dict:
    constant = [c for c in columns if frame[c].nunique(dropna=False) <= 1]
    if constant:
        return check("constant_columns", WARN, f"columns with a single value: {constant}", columns=constant)
    return check("constant_columns", PASS, "no constant columns")


def check_category_variants(frame: pd.DataFrame, columns) -> dict:
    variants = {}
    for c in columns:
        values = frame[c].dropna().astype(str).str.strip()
        groups = values.groupby(values.str.lower()).agg(lambda v: sorted(set(v)))
        clashes = [v for v in groups if len(v) > 1]
        if clashes:
            variants[c] = clashes[:EXAMPLES]
    if variants:
        return check("category_variants", WARN, f"the same category written differently: {variants}", columns=variants)
    return check("category_variants", PASS, "categories written consistently")


def check_multi_valued(frame: pd.DataFrame, columns, separator: str = ";") -> dict:
    counts = {c: int(frame[c].astype(str).str.contains(separator, regex=False).sum()) for c in columns}
    counts = {c: n for c, n in counts.items() if n}
    if counts:
        return check("multi_valued", WARN, f"cells holding several values (split on processing): {counts}", columns=counts)
    return check("multi_valued", PASS, "one value per cell", columns={})


def check_zero_as_missing(frame: pd.DataFrame, value_column: str, unit_column: str | None = None,
                          limit: float = ZERO_SHARE_LIMIT) -> dict:
    values = pd.to_numeric(frame[value_column], errors="coerce")
    share = float((values == 0).mean()) if len(values) else 0.0
    if share > limit:
        unit_note = ""
        if unit_column:
            unit_note = f" (units of the zeros: {frame.loc[values == 0, unit_column].astype(str).value_counts().head(3).to_dict()})"
        return check("zero_as_missing", WARN, f"{share:.1%} of {value_column} values are 0{unit_note}; confirm 0 is a "
                     "measurement, not 'not quantified'", share=round(share, 4))
    return check("zero_as_missing", PASS, f"{share:.1%} of {value_column} values are 0")


# ----------------------------------------------------------------------------
# Runner and QA log
# ----------------------------------------------------------------------------

def run_rai_checks(frame: pd.DataFrame, metadata: dict | None = None, now: datetime | None = None,
                   filled_later=()) -> list[dict]:
    """The add-on checks for a time-series table (processed or analysis-ready), in checklist order."""
    now = now or datetime.now().astimezone()
    results = [
        check_key_fields(frame),
        check_missing_share(frame),
        check_value_range(frame),
        check_percent_convention(frame),
        check_status_symbols(frame),
        check_unit_consistency(frame),
        check_future_dates(frame, now),
        check_period_change(frame),
    ]
    if metadata is not None:
        results += [check_metadata_completeness(metadata, filled_later), check_metadata_consistency(frame, metadata)]
    return results


def to_log_rows(dataset_id: str, results: list[dict], date: str, performed_by: str = "automated (hub)") -> list[dict]:
    """Rows in the standard's QA log layout (sheet 4_QA_Checklist, 'QA log fields')."""
    rows = []
    for i, r in enumerate(results, start=1):
        severity = SEVERITY.get(r["status"], r["status"].upper())
        rows.append({
            "log_id": f"{dataset_id}-{date}-{i:02d}",
            "dataset_id": dataset_id,
            "check_id": CHECK_IDS.get(r["check"], ""),
            "check_performed": r["check"],
            "result": r["detail"],
            "severity": severity,
            "date": date,
            "performed_by": performed_by,
            "method": "Automated",
            "corrective_action_or_note": "",
            "reviewer": "",
            "status": "Closed" if severity in ("PASS", "INFO") else "Open",
        })
    return rows
