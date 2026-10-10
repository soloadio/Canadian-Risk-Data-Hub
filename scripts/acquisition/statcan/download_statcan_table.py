#!/usr/bin/env python3
"""Download Statistics Canada tables into the data hub.
 
HOW TO USE
    1. Add the table to sources/statcan_tables.csv (see below).
    2. From the main repository folder, run:
           python scripts/acquisition/statcan/download_statcan_table.py
    3. Look for SUCCESS in the output.
 
THE CATALOGUE (sources/statcan_tables.csv)
    One row per table. Keep the first line as it is:
 
        table_number,subject,category,geography
        18-10-0005-01,cpi,inflation,
        14-10-0287-01,,labour,
 
    table_number  Required. The number from the table's page, e.g. 18-10-0005-01.
    subject       Optional. One or two words for the dataset name, e.g. cpi.
    category      Optional. A grouping label, e.g. inflation.
    geography     Optional. Which place to keep in the processed file.
                  Empty means Canada. Use all to keep every place.
    Leave optional columns empty, but keep the commas.
 
TO RUN ONE TABLE WITHOUT THE CATALOGUE
    python scripts/acquisition/statcan/download_statcan_table.py --table 18-10-0005-01
 
WHAT YOU GET (for each table, named with its dataset ID, e.g. statcan_18100005_cpi_annual)
    raw/         the original data, untouched
    processed/   a cleaned version: date, geography, series, value, unit, source, dataset_id, status
    metadata/    a description of the dataset
 
It is safe to run again. The output says whether the data changed since last time.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import statistics
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------

WDS_BASE = "https://www150.statcan.gc.ca/t1/wds/rest"
TABLE_PAGE = "https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid={pid}01"
SOURCE_CODE = "statcan"
CATALOGUE_RELATIVE_PATH = Path("sources") / "statcan_tables.csv"
TIMEOUT_SECONDS = 60
MAX_ATTEMPTS = 3
HEADERS = {"User-Agent": "rai-risk-data-hub/0.1 (student proof of concept)"}

# Words dropped when a subject is built from a table title.
TITLE_STOP_WORDS = {
    "a", "an", "and", "annual", "annually", "as", "at", "average", "by", "current", "cycle", "daily",
    "data", "dollars", "for", "from", "in", "last", "levels", "monthly", "months", "not", "of", "on",
    "per", "quarterly", "seasonally", "adjusted", "unadjusted", "table", "the", "to", "trend",
    "weekly", "with",
}


class AcquisitionError(Exception):
    """A problem with one table. The message is shown to the user."""


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


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


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

def http(session, method: str, url: str, **kwargs):
    """GET or POST with a timeout and a few retries on network or server errors."""
    last_problem = "unknown problem"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = getattr(session, method)(url, timeout=TIMEOUT_SECONDS, headers=HEADERS, **kwargs)
        except requests.RequestException as exc:
            last_problem = f"network error: {exc}"
        else:
            if response.status_code == 409:
                raise AcquisitionError(
                    "Statistics Canada returned HTTP 409. The table is probably locked while it is "
                    "being updated (midnight to 8:30 a.m. Eastern). Try again after 8:30 a.m. Eastern."
                )
            if response.status_code >= 500:
                last_problem = f"HTTP {response.status_code}"
            elif response.status_code >= 400:
                raise AcquisitionError(f"HTTP {response.status_code} from {url}")
            else:
                return response
        if attempt < MAX_ATTEMPTS:
            time.sleep(2 ** attempt)
    raise AcquisitionError(f"{last_problem} after {MAX_ATTEMPTS} attempts ({url})")


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
    """Official title and release time. Never stops the run if it fails."""
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


def read_raw(raw_bytes: bytes) -> pd.DataFrame:
    raw = pd.read_csv(io.BytesIO(raw_bytes), encoding="utf-8-sig", dtype=str, keep_default_na=False)
    missing_cols = {"REF_DATE", "GEO", "UOM", "VALUE"} - set(raw.columns)
    if missing_cols:
        raise AcquisitionError(f"unexpected CSV layout, missing columns: {sorted(missing_cols)}")
    return raw


def subject_from_title(title: str) -> str:
    """A short subject for the dataset ID, e.g. 'Consumer Price Index, annual average, ...' -> consumer_price_index."""
    words = [w for w in re.findall(r"[a-z0-9]+", title.lower()) if w not in TITLE_STOP_WORDS]
    return "_".join(words[:3]) or "table"


def build_processed(raw: pd.DataFrame, dataset_id: str, geography: str):
    """Turn the raw Statistics Canada table into the standard layout.

    Output columns: date, geography, series, value, unit, source, dataset_id, status
    """
    columns = list(raw.columns)
    first_dim = columns.index("DGUID") + 1 if "DGUID" in columns else columns.index("GEO") + 1
    dimension_cols = columns[first_dim:columns.index("UOM")]

    info = {
        "rows_raw": len(raw),
        "obs_start": raw["REF_DATE"].min() if len(raw) else None,
        "obs_end": raw["REF_DATE"].max() if len(raw) else None,
        "geographies_in_source": int(raw["GEO"].nunique()),
        "units": sorted(set(raw["UOM"])),
        "geography_filter": geography,
    }

    subset = raw
    if geography.lower() != "all":
        subset = raw[raw["GEO"] == geography]
        if subset.empty:
            info["geography_filter"] = "all (no rows matched %r)" % geography
            subset = raw

    if len(dimension_cols) == 1:
        series = subset[dimension_cols[0]]
    elif dimension_cols:
        series = subset[dimension_cols].apply(lambda row: " | ".join(v for v in row if v), axis=1)
    else:
        series = pd.Series("", index=subset.index)

    scalar = subset["SCALAR_FACTOR"] if "SCALAR_FACTOR" in subset.columns else pd.Series("", index=subset.index)
    unit = subset["UOM"].where(scalar.str.lower().isin(["", "units"]), subset["UOM"] + ", " + scalar)

    date_map = {value: standardize_date(value) for value in subset["REF_DATE"].unique()}
    unparsed = [value for value, parsed in date_map.items() if parsed is None]
    dates = subset["REF_DATE"].map(lambda v: date_map[v] or v)

    value = pd.to_numeric(subset["VALUE"], errors="coerce")
    status = subset["STATUS"] if "STATUS" in subset.columns else pd.Series("", index=subset.index)

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
    info["rows_processed"] = len(processed)
    info["missing_values"] = int(value.isna().sum())
    info["unparsed_dates"] = unparsed
    return processed, info


# ----------------------------------------------------------------------------
# Storing results
# ----------------------------------------------------------------------------

def seasonal_adjustment_from_title(title: str) -> str:
    lowered = title.lower()
    if "not seasonally adjusted" in lowered:
        return "unadjusted"
    if "seasonally adjusted" in lowered:
        return "adjusted"
    return "not stated in title, verify on the table page"


def script_location(root: Path) -> str:
    """Path of this script relative to the hub folder (just the file name if it lives elsewhere)."""
    here = Path(__file__).resolve()
    try:
        return here.relative_to(root.resolve()).as_posix()
    except ValueError:
        return here.name


def acquire_table(table: dict, root: Path, session, default_geography: str, now: datetime) -> dict:
    """Run every step for one table. Returns a result dict; raises AcquisitionError on failure."""
    pid = table["product_id"]
    geography = table.get("geography") or default_geography
    stamp = now.strftime("%Y%m%d")

    zip_bytes = download_zip(session, get_zip_url(session, pid))
    raw_bytes, cube_meta_bytes = extract_csvs(zip_bytes)
    raw = read_raw(raw_bytes)

    frequency = detect_frequency(raw["REF_DATE"].unique())
    cube = fetch_cube_metadata(session, pid)
    title = cube.get("cubeTitleEn") or f"Statistics Canada table {pretty_table_number(pid)}"
    subject = slug(table.get("subject") or "") or subject_from_title(cube.get("cubeTitleEn") or "")
    category = table.get("category") or "uncategorized"
    dataset_id = f"{SOURCE_CODE}_{pid}_{subject}_{frequency}"

    processed, info = build_processed(raw, dataset_id, geography)

    raw_dir = root / "raw" / SOURCE_CODE / dataset_id
    processed_dir = root / "processed" / SOURCE_CODE / dataset_id
    metadata_dir = root / "metadata"
    for folder in (raw_dir, processed_dir, metadata_dir):
        folder.mkdir(parents=True, exist_ok=True)

    raw_path = raw_dir / f"{dataset_id}_{stamp}.csv"
    cube_meta_path = raw_dir / f"{dataset_id}_{stamp}_cube_metadata.csv"
    processed_path = processed_dir / f"{dataset_id}.csv"
    metadata_path = metadata_dir / f"{dataset_id}.json"

    checksum = "sha256:" + hashlib.sha256(raw_bytes).hexdigest()
    previous_checksum = None
    if metadata_path.exists():
        try:
            previous_checksum = json.loads(metadata_path.read_text(encoding="utf-8")).get("checksum")
        except (ValueError, OSError):
            pass

    raw_path.write_bytes(raw_bytes)  # saved exactly as received
    if cube_meta_bytes:
        cube_meta_path.write_bytes(cube_meta_bytes)
    processed.to_csv(processed_path, index=False, encoding="utf-8", na_rep="")

    relative = lambda p: p.relative_to(root).as_posix()
    quality_notes = (
        f"{info['rows_raw']} rows in raw file; {info['rows_processed']} rows in processed subset "
        f"(geography filter: {info['geography_filter']}); {info['missing_values']} observations without a value; "
        f"{len(info['unparsed_dates'])} unrecognized date formats; frequency detected from reference-period spacing."
    )
    metadata = {
        "dataset_id": dataset_id,
        "dataset_name": title,
        "source_url": TABLE_PAGE.format(pid=pid),
        "source_table_or_series_id": pretty_table_number(pid),
        "description": title,
        "subject_category": category,
        "geography": f"{info['geographies_in_source']} geographies in source; processed subset: {info['geography_filter']}",
        "frequency": frequency,
        "observation_start": info["obs_start"],
        "observation_end": info["obs_end"],
        "units": "; ".join(info["units"]),
        "seasonal_adjustment": seasonal_adjustment_from_title(title),
        "retrieval_script": script_location(root),
        "last_retrieved": now.isoformat(timespec="seconds"),
        "raw_file_location": relative(raw_path),
        "processed_file_location": relative(processed_path),
        "update_frequency": None,
        "source_last_updated": cube.get("releaseTime"),
        "checksum": checksum,
        "quality_notes": quality_notes,
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    warnings = []
    if info["unparsed_dates"]:
        warnings.append(f"unrecognized date formats left as-is: {info['unparsed_dates'][:5]}")
    if frequency == "irregular":
        warnings.append("could not detect a regular frequency; check the reference periods")
    if not cube:
        warnings.append("title and release time unavailable; subject taken from the catalogue or defaulted")
    return {
        "dataset_id": dataset_id,
        "frequency": frequency,
        "rows_raw": info["rows_raw"],
        "rows_processed": info["rows_processed"],
        "raw_path": relative(raw_path),
        "raw_megabytes": len(raw_bytes) / 1e6,
        "changed": None if previous_checksum is None else previous_checksum != checksum,
        "warnings": warnings,
    }


# ----------------------------------------------------------------------------
# Command line
# ----------------------------------------------------------------------------

def main(argv=None, session=None) -> int:
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
                        help='Geography kept in processed files (default "Canada"; "all" keeps every geography).')
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
    now = datetime.now().astimezone()
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
        change = {None: "first retrieval", True: "DATA CHANGED since last retrieval", False: "unchanged since last retrieval"}[result["changed"]]
        print(f"  SUCCESS: {result['dataset_id']} ({result['frequency']})")
        print(f"  {result['rows_raw']} raw rows ({result['raw_megabytes']:.1f} MB), {result['rows_processed']} processed rows, {change}")
        print(f"  raw file: {result['raw_path']}")
        for warning in result["warnings"]:
            print(f"  warning: {warning}")

    print(f"\nDone: {len(unique_tables) - failures} succeeded, {failures} failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())