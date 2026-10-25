#!/usr/bin/env python3
"""Download Statistics Canada tables into the data hub.

HOW TO USE
    1. Add the table to sources/statcan_tables.csv (see below).
    2. From the main repository folder, run:
           python scripts/acquisition/statcan/download_statcan_table.py
    3. Look for SUCCESS in the output, and read any warnings under it.

THE CATALOGUE (sources/statcan_tables.csv)
    One row per table. Keep the first line as it is:

        table_number,subject,category,geography
        18-10-0005-01,cpi,inflation,
        14-10-0287-01,labour_force_characteristics,labour,

    table_number  Required. The number from the table's page, e.g. 18-10-0005-01.
    subject       Recommended. One or two words for the dataset ID, e.g. cpi.
    category      Optional. A grouping label, e.g. inflation.
    geography     Optional. Which place to keep in the processed file.
                  Empty means Canada. Use all to keep every place, or separate
                  several places with ; (e.g. Ontario;Quebec).
    Leave optional columns empty, but keep the commas.

TO RUN ONE TABLE WITHOUT THE CATALOGUE
    python scripts/acquisition/statcan/download_statcan_table.py --table 18-10-0005-01

WHAT YOU GET (for each table, named with its dataset ID, e.g. statcan_18100005_cpi_annual)
    raw/         the original data, untouched; a new dated file only when the data changed
    processed/   a cleaned version: date, geography, series, value, unit, source, dataset_id, status
    metadata/    a description of the dataset, with validation results and retrieval history

It is safe to run again. Once a table has a metadata record its dataset ID never changes,
raw files are never overwritten, and the output says whether the data changed since last time.
Quality checks live in scripts/validation/validate_statcan_dataset.py (Statistics Canada-specific)
and scripts/validation/hub_checks.py (shared). If a check fails, the processed file and metadata
record are left as they were. Storage, raw versioning and metadata writing are shared with the
other publishers' scripts in scripts/common/hub_core.py.
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import statistics
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "validation"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "common"))
import validate_statcan_dataset as checks  # noqa: E402  (lives in scripts/validation/)
import hub_core  # noqa: E402  (lives in scripts/common/)
from hub_core import AcquisitionError, slug  # noqa: E402,F401

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------

WDS_BASE = "https://www150.statcan.gc.ca/t1/wds/rest"
TABLE_PAGE = "https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid={pid}01"
SOURCE_CODE = "statcan"
CATALOGUE_RELATIVE_PATH = Path("sources") / "statcan_tables.csv"
HEADERS = {"User-Agent": "rai-risk-data-hub/0.3 (student proof of concept)"}
VALIDATOR = "scripts/validation/validate_statcan_dataset.py"

# Provenance written into every metadata record (see the README, "Common metadata").
SOURCE_ORGANIZATION = "Statistics Canada"
RETRIEVAL_METHOD = (
    "Statistics Canada Web Data Service (WDS) REST API: getFullTableDownloadCSV returns the full-table "
    "CSV zip (data and table description); getCubeMetadata returns the title, release time, frequency "
    "and expected number of data points."
)
LICENCE = {
    "name": "Statistics Canada Open Licence",
    "url": "https://www.statcan.gc.ca/en/terms-conditions/open-licence",
}
ACCESS = "Public. Free download, no account or API key required."
AI_USE_NOTE = (
    "The acquisition, processing and validation code was written with the help of an AI coding assistant "
    "(Claude, Anthropic) and reviewed by the project team. No data values were generated, edited or imputed "
    "by AI: raw files are stored exactly as published by Statistics Canada, and the processed file is produced "
    "by the deterministic code named in retrieval_script. The automated checks recorded under validation "
    "were run on this retrieval."
)

# StatCan frequency codes (WDS getCodeSets) that map onto the hub's frequency vocabulary.
STATCAN_FREQUENCIES = {
    1: "daily", 2: "weekly", 4: "biweekly", 6: "monthly", 9: "quarterly", 11: "semiannual", 12: "annual",
}

# Words dropped when a subject is built from a table title.
TITLE_STOP_WORDS = {
    "a", "an", "and", "annual", "annually", "as", "at", "average", "by", "current", "cycle", "daily",
    "data", "dollars", "for", "from", "in", "last", "levels", "monthly", "months", "not", "of", "on",
    "per", "quarterly", "seasonally", "adjusted", "unadjusted", "table", "the", "to", "trend",
    "weekly", "with",
    # Geography is left out of the dataset ID (see the README naming conventions).
    "canada", "province", "provinces", "region", "regions", "territory", "territories",
}


# ----------------------------------------------------------------------------
# Table numbers and the catalogue
# ----------------------------------------------------------------------------

def parse_table_number(text: str) -> str:
    """18-10-0005-01, 18100005 or 1810000501 -> '18100005'. Raises ValueError otherwise."""
    digits = re.sub(r"\D", "", text)
    if len(digits) == 10:  # the last two digits are the table "view" suffix
        digits = digits[:8]
    if len(digits) != 8:
        raise ValueError(f"{text!r} is not a Statistics Canada table number (expected like 18-10-0005-01)")
    return digits


def parse_table_arg(text: str) -> dict:
    """Command-line form: NUMBER[:subject[:category[:geography]]]."""
    parts = text.split(":")
    if len(parts) > 4:
        raise argparse.ArgumentTypeError(
            "use NUMBER[:subject[:category[:geography]]], e.g. 18-10-0005-01:cpi:inflation "
            "(frequency is now detected automatically)"
        )
    try:
        product_id = parse_table_number(parts[0])
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc))
    parts += [""] * (4 - len(parts))
    return {"product_id": product_id, "subject": parts[1], "category": parts[2], "geography": parts[3]}


def load_catalogue(path: Path) -> list[dict]:
    """Read the catalogue CSV. Raises ValueError with a clear message if it is malformed."""
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "table_number" not in [f.strip() for f in reader.fieldnames]:
            raise ValueError(f"{path} must have a header row with at least a table_number column")
        tables = []
        for line_number, row in enumerate(reader, start=2):
            row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
            if not row.get("table_number"):
                continue  # blank line
            try:
                product_id = parse_table_number(row["table_number"])
            except ValueError as exc:
                raise ValueError(f"{path}, line {line_number}: {exc}")
            tables.append({
                "product_id": product_id,
                "subject": row.get("subject", ""),
                "category": row.get("category", ""),
                "geography": row.get("geography", ""),
            })
    return tables


def pretty_table_number(product_id: str) -> str:
    return f"{product_id[:2]}-{product_id[2:4]}-{product_id[4:]}-01"


# ----------------------------------------------------------------------------
# Talking to Statistics Canada
# ----------------------------------------------------------------------------

def locked_table(response) -> None:
    if response.status_code == 409:
        raise AcquisitionError(
            "Statistics Canada returned HTTP 409. The table is probably locked while it is "
            "being updated (midnight to 8:30 a.m. Eastern). Try again after 8:30 a.m. Eastern."
        )


def http(session, method: str, url: str, **kwargs):
    """GET or POST with a timeout and a few retries on network or server errors."""
    return hub_core.http(session, method, url, headers=HEADERS, on_status=locked_table, **kwargs)


def get_zip_url(session, product_id: str) -> str:
    """Ask the WDS where the full-table CSV zip lives."""
    response = http(session, "get", f"{WDS_BASE}/getFullTableDownloadCSV/{product_id}/en")
    try:
        payload = response.json()
    except ValueError:
        raise AcquisitionError("the download service did not return JSON")
    if payload.get("status") != "SUCCESS" or not payload.get("object"):
        raise AcquisitionError(f"unexpected response from the download service: {payload}")
    return payload["object"]


def download_zip(session, url: str) -> bytes:
    response = http(session, "get", url, stream=True)
    data = b"".join(response.iter_content(chunk_size=1024 * 1024))
    if not data:
        raise AcquisitionError("the downloaded file was empty")
    return data


def extract_csvs(zip_bytes: bytes):
    """Return (data_csv_bytes, cube_metadata_csv_bytes_or_None) from the zip."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile:
        raise AcquisitionError("the download was not a valid zip file")
    names = archive.namelist()
    csv_names = [n for n in names if n.lower().endswith(".csv")]
    data_names = [n for n in csv_names if "metadata" not in n.lower()]
    meta_names = [n for n in csv_names if "metadata" in n.lower()]
    if not data_names:
        raise AcquisitionError(f"no data CSV found in the zip (it contains: {names})")
    meta_bytes = archive.read(meta_names[0]) if meta_names else None
    return archive.read(data_names[0]), meta_bytes


def fetch_cube_metadata(session, product_id: str) -> dict:
    """Official title, release time, frequency and size. Never stops the run if it fails."""
    try:
        response = http(session, "post", f"{WDS_BASE}/getCubeMetadata", json=[{"productId": int(product_id)}])
        item = response.json()[0]
        if item.get("status") == "SUCCESS":
            return item["object"]
    except (AcquisitionError, ValueError, KeyError, IndexError, TypeError) as exc:
        print(f"  warning: could not read table metadata from the service ({exc})", file=sys.stderr)
    return {}


# ----------------------------------------------------------------------------
# Reading and standardizing
# ----------------------------------------------------------------------------

DATE_PATTERN = re.compile(r"^\d{4}(-\d{2}){0,2}$")
QUARTER_PATTERN = re.compile(r"^(\d{4})-Q([1-4])$")


def standardize_date(ref_date: str) -> str | None:
    """2025 -> 2025-01-01, 2026-08 -> 2026-08-01, 2025-Q3 -> 2025-07-01, full dates stay. None if unrecognized."""
    quarter = QUARTER_PATTERN.match(ref_date)
    if quarter:
        return f"{quarter.group(1)}-{(int(quarter.group(2)) - 1) * 3 + 1:02d}-01"
    if not DATE_PATTERN.match(ref_date):
        return None
    return {4: f"{ref_date}-01-01", 7: f"{ref_date}-01", 10: ref_date}[len(ref_date)]


def detect_frequency(ref_dates) -> str:
    """Work out daily/weekly/monthly/... from the typical gap between reference periods."""
    parsed = pd.to_datetime(
        pd.Series([standardize_date(v) for v in set(ref_dates)]).dropna(), errors="coerce"
    ).dropna().sort_values().drop_duplicates()
    if len(parsed) < 2:
        return "irregular"
    typical_gap = statistics.median(parsed.diff().dropna().dt.days)
    for upper, name in [(1.5, "daily"), (8, "weekly"), (15, "biweekly"), (32, "monthly"),
                        (93, "quarterly"), (186, "semiannual"), (370, "annual")]:
        if typical_gap <= upper:
            return name
    return "irregular"


def choose_frequency(cube: dict, ref_dates) -> tuple[str, str]:
    """Frequency for the dataset ID: the one Statistics Canada declares, else detected from the dates.

    Detection alone is unstable: a survey that moves from annual to every second year changes the
    median gap and would rename the dataset. Returns (frequency, how it was determined).
    """
    declared = STATCAN_FREQUENCIES.get(cube.get("frequencyCode"))
    if declared:
        return declared, "declared by Statistics Canada"
    return detect_frequency(ref_dates), "detected from reference-period spacing"


def read_raw(raw_bytes: bytes) -> pd.DataFrame:
    raw = pd.read_csv(io.BytesIO(raw_bytes), encoding="utf-8-sig", dtype=str, keep_default_na=False)
    missing_cols = {"REF_DATE", "GEO", "UOM", "VALUE"} - set(raw.columns)
    if missing_cols:
        raise AcquisitionError(f"unexpected CSV layout, missing columns: {sorted(missing_cols)}")
    return raw


def dimension_columns(raw: pd.DataFrame) -> list[str]:
    """The table's own dimensions: the columns between GEO/DGUID and UOM."""
    columns = list(raw.columns)
    first_dim = columns.index("DGUID") + 1 if "DGUID" in columns else columns.index("GEO") + 1
    return columns[first_dim:columns.index("UOM")]


def subject_from_title(title: str) -> str:
    """A short subject for the dataset ID, e.g. 'Consumer Price Index, annual average, ...' -> consumer_price_index."""
    words = [w for w in re.findall(r"[a-z0-9]+", title.lower()) if w not in TITLE_STOP_WORDS]
    return "_".join(words[:3]) or "table"


def build_processed(raw: pd.DataFrame, dataset_id: str, geographies: list[str]):
    """Turn the raw Statistics Canada table into the standard layout for the chosen geographies.

    Output columns: date, geography, series, value, unit, source, dataset_id, status
    """
    dims = dimension_columns(raw)
    iso_dates = sorted(d for d in (standardize_date(v) for v in raw["REF_DATE"].unique()) if d)
    subset = raw[raw["GEO"].isin(geographies)]

    if len(dims) == 1:
        series = subset[dims[0]]
    elif dims:
        series = subset[dims].apply(lambda row: " | ".join(v for v in row if v), axis=1)
    else:
        series = pd.Series("", index=subset.index)

    scalar = subset["SCALAR_FACTOR"] if "SCALAR_FACTOR" in subset.columns else pd.Series("", index=subset.index)
    unit = subset["UOM"].where(scalar.str.lower().isin(["", "units"]), subset["UOM"] + ", " + scalar)

    date_map = {value: standardize_date(value) for value in subset["REF_DATE"].unique()}
    unparsed = [value for value, parsed in date_map.items() if parsed is None]
    dates = subset["REF_DATE"].map(lambda v: date_map[v] or v)

    raw_value = subset["VALUE"].str.strip()
    value = pd.to_numeric(raw_value, errors="coerce")
    non_numeric = sorted(set(raw_value[value.isna() & raw_value.ne("")]))
    status = subset["STATUS"].str.strip() if "STATUS" in subset.columns else pd.Series("", index=subset.index)

    processed = pd.DataFrame({
        "date": dates,
        "geography": subset["GEO"],
        "series": series,
        "value": value,
        "unit": unit,
        "source": SOURCE_CODE,
        "dataset_id": dataset_id,
        "status": status,
    })
    info = {
        "rows_raw": len(raw),
        "obs_start": iso_dates[0] if iso_dates else None,
        "obs_end": iso_dates[-1] if iso_dates else None,
        "geographies_in_source": int(raw["GEO"].nunique()),
        "units": sorted(set(raw["UOM"])),
        "unparsed_dates": unparsed,
        "non_numeric": non_numeric,
    }
    return processed, info, subset


# ----------------------------------------------------------------------------
# One table, end to end
# ----------------------------------------------------------------------------

def release_time_with_offset(release_time: str | None) -> str | None:
    """StatCan release times are Eastern time without an offset: '2026-09-18T08:30' -> '2026-09-18T08:30:00-04:00'."""
    if not release_time:
        return None
    try:
        local = datetime.fromisoformat(release_time)
    except ValueError:
        return release_time
    if local.tzinfo is None:
        local = local.replace(tzinfo=ZoneInfo("America/Toronto"))
    return local.isoformat(timespec="seconds")


def acquire_table(table: dict, root: Path, session, default_geography: str, now: datetime) -> dict:
    """Run every step for one table. Returns a result dict; raises AcquisitionError on failure."""
    pid = table["product_id"]
    geography = table.get("geography") or default_geography
    metadata_dir = root / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)

    zip_bytes = download_zip(session, get_zip_url(session, pid))
    raw_bytes, cube_meta_bytes = extract_csvs(zip_bytes)
    raw = read_raw(raw_bytes)
    cube = fetch_cube_metadata(session, pid)

    # 1. A stable dataset ID.
    frequency, frequency_basis = choose_frequency(cube, raw["REF_DATE"].unique())
    title = cube.get("cubeTitleEn") or f"Statistics Canada table {pretty_table_number(pid)}"
    subject = slug(table.get("subject") or "") or subject_from_title(cube.get("cubeTitleEn") or "")
    category = slug(table.get("category") or "") or "uncategorized"
    dataset_id, id_note = hub_core.resolve_dataset_id(metadata_dir, f"{SOURCE_CODE}_{pid}_",
                                                      f"{SOURCE_CODE}_{pid}_{subject}_{frequency}",
                                                      f"table {pretty_table_number(pid)}")
    # The frequency in a pinned ID is the one the dataset is known by; keep the metadata consistent with it.
    if id_note:
        frequency, frequency_basis = dataset_id.rsplit("_", 1)[-1], "taken from the existing dataset ID"

    # 2. Keep the raw data as received, as a new version only when it changed.
    ds = hub_core.open_dataset(root, SOURCE_CODE, dataset_id, now.strftime("%Y%m%d"), raw_bytes,
                               ".csv", {"_cube_metadata.csv": cube_meta_bytes})
    if id_note:
        ds.notes.append(id_note)
    if not cube:
        ds.notes.append("title, release time and frequency unavailable from the service; subject and frequency "
                        "taken from the catalogue or detected")

    # 3. Statistics Canada-specific checks, then standardize.
    dims = dimension_columns(raw)
    schema = checks.schema_of(raw, dims)
    schema_result, schema_version = checks.check_schema(schema, ds.previous.get("schema"), ds.previous.get("schema_version"))
    geographies, geography_result = checks.select_geographies(raw, geography)
    results = [schema_result, checks.check_row_count(len(raw), cube.get("nbDatapointsCube")), geography_result]
    if geography_result["status"] == checks.FAIL:
        raise AcquisitionError(f"{geography_result['detail']} (raw file kept: {ds.relative(ds.raw_path)})")

    processed, info, raw_subset = build_processed(raw, dataset_id, geographies)
    results += [checks.check_dates(info["unparsed_dates"]), checks.check_numeric(info["non_numeric"]),
                checks.check_terminated(raw_subset)]

    # 4. Shared checks, then write the processed file and the metadata record.
    record = {
        "dataset_id": dataset_id,
        "dataset_name": title,
        "description": title,
        "source_organization": SOURCE_ORGANIZATION,
        "source_url": TABLE_PAGE.format(pid=pid),
        "source_table_or_series_id": pretty_table_number(pid),
        "licence": LICENCE,
        "access": ACCESS,
        "retrieval_method": RETRIEVAL_METHOD,
        "retrieval_script": hub_core.script_location(__file__, root),
        "subject_category": category,
        "geography": f"{info['geographies_in_source']} geographies in source; processed file: "
                     + ("all" if geography.strip().lower() == "all" else "; ".join(geographies)),
        "frequency": frequency,
        "frequency_basis": frequency_basis,
        "observation_start": info["obs_start"],
        "observation_end": info["obs_end"],
        "units": "; ".join(info["units"]),
    }
    coverage = "all geographies" if len(geographies) == info["geographies_in_source"] else ", ".join(geographies)
    return hub_core.finish_dataset(
        ds, processed=processed, frequency=frequency, results=results, now=now, record=record,
        source_last_updated=release_time_with_offset(cube.get("releaseTime")), schema=schema,
        schema_version=schema_version, rows_raw=info["rows_raw"], coverage=coverage, validator=VALIDATOR,
        ai_use_note=AI_USE_NOTE, missing_status=checks.MISSING_STATUS,
    )


# ----------------------------------------------------------------------------
# Command line
# ----------------------------------------------------------------------------

def main(argv=None, session=None, now=None) -> int:
    parser = argparse.ArgumentParser(
        description="Download Statistics Canada tables into the data hub.",
        epilog="Default tables come from sources/statcan_tables.csv under the hub folder.",
    )
    parser.add_argument("--table", action="append", type=parse_table_arg, metavar="NUMBER[:subject[:category[:geography]]]",
                        help="Table to download, e.g. 18-10-0005-01:cpi:inflation (repeatable). Overrides the catalogue.")
    parser.add_argument("--catalogue", type=Path, default=None,
                        help=f"Catalogue CSV (default: <repo-root>/{CATALOGUE_RELATIVE_PATH.as_posix()}).")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[3],
                        help="Hub root folder (default: three levels above this script).")
    parser.add_argument("--geography", default="Canada",
                        help='Geography kept in processed files (default "Canada"; "all" keeps every geography; '
                             'separate several with ";").')
    args = parser.parse_args(argv)

    if args.table:
        tables = args.table
    else:
        catalogue_path = args.catalogue or (args.repo_root / CATALOGUE_RELATIVE_PATH)
        if not catalogue_path.exists():
            print(f"No tables given and no catalogue found at {catalogue_path}.\n"
                  f"Create it (header: table_number,subject,category,geography) or pass --table.", file=sys.stderr)
            return 2
        try:
            tables = load_catalogue(catalogue_path)
        except ValueError as exc:
            print(f"Catalogue problem: {exc}", file=sys.stderr)
            return 2
        if not tables:
            print(f"The catalogue {catalogue_path} has no tables yet.", file=sys.stderr)
            return 2

    seen, unique_tables = set(), []
    for table in tables:
        if table["product_id"] in seen:
            print(f"warning: table {pretty_table_number(table['product_id'])} listed twice, using the first entry", file=sys.stderr)
            continue
        seen.add(table["product_id"])
        unique_tables.append(table)

    session = session or requests.Session()
    now = now or datetime.now().astimezone()
    print(f"Statistics Canada acquisition, {now.isoformat(timespec='seconds')}")
    print(f"Hub folder: {args.repo_root}")

    failures = 0
    for table in unique_tables:
        print(f"\nTable {pretty_table_number(table['product_id'])}")
        try:
            result = acquire_table(table, args.repo_root, session, args.geography, now)
        except AcquisitionError as exc:
            failures += 1
            print(f"  FAILED: {exc}")
            continue
        hub_core.print_result(result)

    print(f"\nDone: {len(unique_tables) - failures} succeeded, {failures} failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
