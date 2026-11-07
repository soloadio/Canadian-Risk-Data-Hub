#!/usr/bin/env python3
"""Download Bank of Canada series into the data hub, through the Valet API.

HOW TO USE
    1. Add the series to sources/boc_series.csv (see below).
    2. From the main repository folder, run:
           python scripts/acquisition/boc/download_boc_series.py
    3. Look for SUCCESS in the output, and read any warnings under it.

THE CATALOGUE (sources/boc_series.csv)
    One row per dataset. Keep the first line as it is:

        series_ids,id_part,subject,name,category,description,update_frequency,refresh_schedule,revision_policy,priority,known_limitations
        V39079,,policy_rate,,interest_rates,,business daily,11:30 ET business days,none,high,Not revised
        BD.CDN.2YR.DQ.YLD;BD.CDN.10YR.DQ.YLD,bdcdnyld,bond_yields,Benchmark bond yields,interest_rates,...

    series_ids        Required. One Valet series name, or several separated by ; to keep them in one dataset.
    id_part           The publisher-ID part of the dataset ID. Required when a row has several series;
                      for one series it defaults to the series name without punctuation (V39079 -> v39079).
    subject           Required. One or two words for the dataset ID, e.g. policy_rate.
    name              Optional for one series (the Bank's label is used); recommended for several.
    category          Optional. A grouping label, e.g. interest_rates.
    description       Optional. Overrides the Bank's own description of the series.
    update_frequency, refresh_schedule, revision_policy, priority, known_limitations
                      Optional. Copied into the metadata record as written (see the README, "Common metadata").

TO RUN SOME DATASETS ONLY
    python scripts/acquisition/boc/download_boc_series.py --only policy_rate --only corra

TO REBUILD THE PROCESSED FILES WITHOUT DOWNLOADING (after a change to the processing code)
    python scripts/acquisition/boc/download_boc_series.py --from-raw

WHAT YOU GET (for each dataset, named with its dataset ID, e.g. boc_v39079_policy_rate_daily)
    raw/boc/<id>/        the Valet JSON response, unchanged; a new dated file only when the data changed
    processed/boc/<id>/  date, geography, series, value, unit, source, dataset_id, status, status_category,
                         geo_code (blank: the Bank publishes no geography code), series_id (the Valet series name)
    metadata/<id>.json   a description of the dataset, with validation results and retrieval history

Every run downloads each series' full history (a few hundred kB at most), so the raw file is always
the complete published series and its checksum shows whether anything changed, including revisions.
Only observations with a value are kept: on holidays the Bank omits market series rather than
publishing an empty value, and no empty row is created for them.
Storage, raw versioning, the shared checks and metadata writing come from scripts/common/hub_core.py.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "validation"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "common"))
import hub_checks as checks  # noqa: E402  (lives in scripts/validation/)
import hub_core  # noqa: E402  (lives in scripts/common/)
from hub_core import AcquisitionError, slug  # noqa: E402

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------

VALET_BASE = "https://www.bankofcanada.ca/valet"
SOURCE_CODE = "boc"
CATALOGUE_RELATIVE_PATH = Path("sources") / "boc_series.csv"
HEADERS = {"User-Agent": "rai-risk-data-hub/0.3 (student proof of concept)"}
VALIDATOR = "scripts/validation/hub_checks.py"
CATALOGUE_COLUMNS = ["series_ids", "id_part", "subject", "name", "category", "description", *hub_core.GOVERNANCE_FIELDS]

# Provenance written into every metadata record (see the README, "Common metadata").
SOURCE_ORGANIZATION = "Bank of Canada"
RETRIEVAL_METHOD = (
    "Bank of Canada Valet REST API: observations/{series}/json returns the full history of the requested series "
    "together with each series' label, description, unit and frequency. The response is stored unchanged as the raw file."
)
TERMS_URL = "https://www.bankofcanada.ca/terms/"
LICENCE_CONDITIONS = (
    "Free to use, copy and distribute. Attribute the Bank of Canada as the source and indicate any changes. "
    "Bank of Canada exchange rates are for statistical and analytical purposes only, not for executing trades."
)
ACCESS = "Public. Free, no account or API key required. The Bank's terms prohibit circumventing its request limits."
ACCESS_FORMAT = "JSON from a REST API (Valet)"
AI_USE_NOTE = (
    "The acquisition, processing and validation code was written with the help of an AI coding assistant "
    "(Claude, Anthropic) and reviewed by the project team. No data values were generated, edited or imputed "
    "by AI: raw files are stored exactly as published by the Bank of Canada, and the processed file is produced "
    "by the deterministic code named in retrieval_script. The automated checks recorded under validation "
    "were run on this retrieval."
)

# Valet "frequency" words that map onto the hub's frequency vocabulary.
VALET_FREQUENCIES = {
    "business daily": "daily", "daily": "daily", "weekly": "weekly", "monthly": "monthly",
    "quarterly": "quarterly", "semi-annual": "semiannual", "annual": "annual",
}
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ----------------------------------------------------------------------------
# The catalogue
# ----------------------------------------------------------------------------

def default_id_part(series_ids: list[str]) -> str:
    return re.sub(r"[^a-z0-9]", "", series_ids[0].lower())


def load_catalogue(path: Path) -> list[dict]:
    """Read the catalogue CSV. Raises ValueError with a clear message if it is malformed."""
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "series_ids" not in [f.strip() for f in reader.fieldnames]:
            raise ValueError(f"{path} must have a header row with at least a series_ids column")
        datasets = []
        for line_number, row in enumerate(reader, start=2):
            row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
            series_ids = [s.strip() for s in row.get("series_ids", "").split(";") if s.strip()]
            if not series_ids:
                continue  # blank line
            subject = slug(row.get("subject", ""))
            if not subject:
                raise ValueError(f"{path}, line {line_number}: subject is required (it becomes part of the dataset ID)")
            id_part = re.sub(r"[^a-z0-9]", "", row.get("id_part", "").lower())
            if len(series_ids) > 1 and not id_part:
                raise ValueError(f"{path}, line {line_number}: id_part is required when a row lists several series")
            datasets.append({
                "series_ids": series_ids,
                "id_part": id_part or default_id_part(series_ids),
                "subject": subject,
                "name": row.get("name", ""),
                "category": row.get("category", ""),
                "description": row.get("description", ""),
                **hub_core.governance_from(row),
            })
    return datasets


# ----------------------------------------------------------------------------
# Talking to the Bank of Canada
# ----------------------------------------------------------------------------

def unknown_series(response) -> None:
    """Valet answers 404 with a JSON message when a series name does not exist."""
    if response.status_code == 404:
        try:
            message = response.json().get("message", "")
        except ValueError:
            message = ""
        raise AcquisitionError(f"the Bank of Canada does not recognize this series ({message or 'HTTP 404'}); "
                               "check the name in sources/boc_series.csv")


def fetch_observations(session, series_ids: list[str]) -> bytes:
    """The Valet JSON response for the full history of the given series, as received."""
    url = f"{VALET_BASE}/observations/{','.join(series_ids)}/json"
    response = hub_core.http(session, "get", url, headers=HEADERS, on_status=unknown_series)
    if not response.content:
        raise AcquisitionError("the Bank of Canada returned an empty response")
    return response.content


def read_payload(raw_bytes: bytes) -> dict:
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise AcquisitionError("the Bank of Canada did not return JSON (the service may be down)")
    if not isinstance(payload.get("observations"), list) or not isinstance(payload.get("seriesDetail"), dict):
        raise AcquisitionError(f"unexpected response layout, top-level keys: {sorted(payload)}")
    return payload


# ----------------------------------------------------------------------------
# Reading and standardizing
# ----------------------------------------------------------------------------

def unit_of(detail: dict) -> str:
    return ((detail.get("metadata") or {}).get("unit") or "").strip()


def choose_frequency(payload: dict, series_ids: list[str]) -> tuple[str, str]:
    """The frequency the Bank declares for the series. All series in one dataset must share it."""
    declared = {(payload["seriesDetail"].get(s, {}).get("metadata") or {}).get("frequency", "") for s in series_ids}
    declared.discard("")
    if len(declared) > 1:
        raise AcquisitionError(f"the series in this dataset have different frequencies ({sorted(declared)}); "
                               "list them as separate rows in the catalogue")
    if declared:
        word = declared.pop()
        if word.lower() in VALET_FREQUENCIES:
            return VALET_FREQUENCIES[word.lower()], f"declared by Bank of Canada ({word})"
    return "irregular", "not declared by Bank of Canada in a recognized form"


def build_processed(payload: dict, dataset_id: str, series_ids: list[str]):
    """Turn the Valet response into the standard layout.

    One row per date and series where the Bank published a value. Holidays, when the Bank omits a
    market series from that date, produce no row (not an empty value).
    Output columns: date, geography, series, value, unit, source, dataset_id, status, status_category,
    geo_code, series_id. series_id is the Valet series name (e.g. BD.CDN.10YR.DQ.YLD), which stays the
    same if the Bank rewords the label.
    """
    details = payload["seriesDetail"]
    rows, unparsed, non_numeric, empty = [], set(), set(), 0
    for observation in payload["observations"]:
        date = str(observation.get("d", ""))
        if not ISO_DATE.match(date):
            unparsed.add(date)
        for sid in series_ids:
            cell = observation.get(sid)
            if not isinstance(cell, dict) or "v" not in cell:
                continue  # not published on this date
            text = str(cell["v"]).strip()
            if text == "":
                empty += 1
                continue
            try:
                value = float(text)
            except ValueError:
                non_numeric.add(text)
                continue
            # Any per-observation flag the Bank adds (none are published today) is kept as the status.
            status = ";".join(f"{k}={v}" for k, v in cell.items() if k != "v")
            rows.append({"date": date, "geography": "Canada", "series": details.get(sid, {}).get("label") or sid,
                         "value": value, "unit": unit_of(details.get(sid, {})), "source": SOURCE_CODE,
                         "dataset_id": dataset_id, "status": status, "status_category": "", "geo_code": "",
                         "series_id": sid})
    processed = pd.DataFrame(rows, columns=checks.PROCESSED_COLUMNS)
    processed["status_category"] = checks.status_category(processed["status"])
    dates = sorted(d for d in processed["date"] if ISO_DATE.match(d))
    info = {
        "rows_raw": len(payload["observations"]),
        "obs_start": dates[0] if dates else None,
        "obs_end": dates[-1] if dates else None,
        "units": sorted({unit_of(details.get(s, {})) for s in series_ids} - {""}),
        "unparsed_dates": sorted(unparsed),
        "non_numeric": sorted(non_numeric),
        "empty_values": empty,
        "values_by_series": processed["series_id"].value_counts().to_dict(),
    }
    return processed, info


# ----------------------------------------------------------------------------
# Bank of Canada-specific checks
# ----------------------------------------------------------------------------

def check_series_present(payload: dict, series_ids: list[str], values_by_series: dict) -> dict:
    """Every requested series is described in the response and has at least one value."""
    undescribed = [s for s in series_ids if s not in payload["seriesDetail"]]
    empty = [s for s in series_ids if not values_by_series.get(s)]
    if undescribed or empty:
        return checks.check("series_present", checks.FAIL,
                            f"requested series missing from the response: {undescribed or empty}", requested=series_ids)
    counts = ", ".join(f"{s}: {values_by_series[s]}" for s in series_ids)
    return checks.check("series_present", checks.PASS, f"all {len(series_ids)} series returned ({counts} values)",
                        values_by_series={s: int(values_by_series[s]) for s in series_ids})


def check_units(payload: dict, series_ids: list[str]) -> dict:
    """Series kept in one dataset should share a unit, or comparisons across them mislead."""
    units = {s: unit_of(payload["seriesDetail"].get(s, {})) for s in series_ids}
    distinct = sorted(set(units.values()))
    if "" in distinct:
        return checks.check("units", checks.WARN, f"no unit published for {[s for s, u in units.items() if not u]}")
    if len(distinct) > 1:
        return checks.check("units", checks.WARN, f"series in this dataset use different units: {units}")
    return checks.check("units", checks.PASS, f"all series in {distinct[0]}")


def check_terms(payload: dict) -> dict:
    url = (payload.get("terms") or {}).get("url")
    if url:
        return checks.check("terms", checks.PASS, f"response carries the Bank's terms of use ({url})")
    return checks.check("terms", checks.WARN, "the response did not include the terms-of-use link; check the licence")


def check_empty_values(empty: int) -> dict:
    if empty:
        return checks.check("empty_values", checks.WARN, f"{empty} observations carried an empty value and were left out")
    return checks.check("empty_values", checks.PASS, "no observation carried an empty value")


# ----------------------------------------------------------------------------
# One dataset, end to end
# ----------------------------------------------------------------------------

def stored_response(root: Path, entry: dict) -> bytes:
    """The stored raw Valet response for a catalogue row, for reprocessing without a download."""
    records = sorted((root / "metadata").glob(f"{SOURCE_CODE}_{entry['id_part']}_*.json"))
    if len(records) != 1:
        raise AcquisitionError(f"series {', '.join(entry['series_ids'])} need exactly one metadata record to reprocess "
                               f"(found {len(records)}); run without --from-raw to download them")
    return hub_core.stored_raw(root, hub_core.read_previous_metadata(records[0]))


def acquire_dataset(entry: dict, root: Path, session, now: datetime, from_raw: bool = False) -> dict:
    """Run every step for one catalogue row. Returns a result dict; raises AcquisitionError on failure.

    from_raw=True rebuilds the processed file and metadata from the stored raw file instead of downloading.
    """
    series_ids = entry["series_ids"]
    metadata_dir = root / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)

    raw_bytes = stored_response(root, entry) if from_raw else fetch_observations(session, series_ids)
    payload = read_payload(raw_bytes)

    # 1. A stable dataset ID.
    frequency, frequency_basis = choose_frequency(payload, series_ids)
    prefix = f"{SOURCE_CODE}_{entry['id_part']}_"
    dataset_id, id_note = hub_core.resolve_dataset_id(metadata_dir, prefix, f"{prefix}{entry['subject']}_{frequency}",
                                                      f"series {', '.join(series_ids)}")
    if id_note:
        frequency, frequency_basis = dataset_id.rsplit("_", 1)[-1], "taken from the existing dataset ID"

    # 2. Keep the raw response as received, as a new version only when it changed.
    ds = hub_core.open_dataset(root, SOURCE_CODE, dataset_id, now.strftime("%Y%m%d"), raw_bytes, ".json")
    if id_note:
        ds.notes.append(id_note)

    # 3. Bank of Canada-specific checks, then standardize.
    details = payload["seriesDetail"]
    # Sorted, because the order of series inside seriesDetail is not part of the layout.
    schema = checks.schema_of(["d", *sorted(details)], [], [unit_of(d) for d in details.values()])
    schema_result, schema_version = checks.check_schema(schema, ds.previous.get("schema"), ds.previous.get("schema_version"))
    processed, info = build_processed(payload, dataset_id, series_ids)
    results = [schema_result, check_terms(payload),
               check_series_present(payload, series_ids, info["values_by_series"]),
               check_units(payload, series_ids), checks.check_dates(info["unparsed_dates"]),
               checks.check_numeric(info["non_numeric"]), check_empty_values(info["empty_values"])]

    # 4. Shared checks, then write the processed file and the metadata record.
    labels = [details.get(s, {}).get("label") or s for s in series_ids]
    if len(series_ids) == 1:
        name = entry["name"] or labels[0]
        description = details.get(series_ids[0], {}).get("description") or name
    else:
        name = entry["name"] or "; ".join(labels)
        description = "; ".join(f"{s}: {label}" for s, label in zip(series_ids, labels))
    description = (entry.get("description") or "").strip() or description
    record = {
        "dataset_id": dataset_id,
        "dataset_name": name,
        "description": description,
        "source_organization": SOURCE_ORGANIZATION,
        "source_url": f"{VALET_BASE}/observations/{','.join(series_ids)}/json",
        "source_table_or_series_id": ", ".join(series_ids),
        "licence": {
            "name": "Bank of Canada Terms of Use",
            "url": (payload.get("terms") or {}).get("url") or TERMS_URL,
            "conditions": LICENCE_CONDITIONS,
        },
        "access": ACCESS,
        "access_format": ACCESS_FORMAT,
        "retrieval_method": RETRIEVAL_METHOD,
        "retrieval_script": hub_core.script_location(__file__, root),
        "subject_category": slug(entry["category"]) or "uncategorized",
        "geography": "Canada (national series); processed file: Canada",
        "frequency": frequency,
        "frequency_basis": frequency_basis,
        "observation_start": info["obs_start"],
        "observation_end": info["obs_end"],
        "units": "; ".join(info["units"]),
    }
    return hub_core.finish_dataset(
        ds, processed=processed, frequency=frequency, results=results, now=now, record=record,
        source_last_updated=None, schema=schema, schema_version=schema_version, rows_raw=info["rows_raw"],
        coverage="Canada", validator=VALIDATOR, ai_use_note=AI_USE_NOTE,
        governance=entry, reprocessed=from_raw,
    )


# ----------------------------------------------------------------------------
# Command line
# ----------------------------------------------------------------------------

def main(argv=None, session=None, now=None) -> int:
    parser = argparse.ArgumentParser(
        description="Download Bank of Canada series into the data hub (Valet API).",
        epilog="Datasets come from sources/boc_series.csv under the hub folder.",
    )
    parser.add_argument("--only", action="append", metavar="SUBJECT",
                        help="Download only the catalogue rows with this subject, e.g. policy_rate (repeatable).")
    parser.add_argument("--from-raw", action="store_true",
                        help="Rebuild processed files and metadata from the stored raw files, without downloading "
                             "(after a change to the processing code). Records no new retrieval.")
    parser.add_argument("--catalogue", type=Path, default=None,
                        help=f"Catalogue CSV (default: <repo-root>/{CATALOGUE_RELATIVE_PATH.as_posix()}).")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[3],
                        help="Hub root folder (default: three levels above this script).")
    args = parser.parse_args(argv)

    catalogue_path = args.catalogue or (args.repo_root / CATALOGUE_RELATIVE_PATH)
    if not catalogue_path.exists():
        print(f"No catalogue found at {catalogue_path}.\n"
              f"Create it (header: {','.join(CATALOGUE_COLUMNS)}).", file=sys.stderr)
        return 2
    try:
        datasets = load_catalogue(catalogue_path)
    except ValueError as exc:
        print(f"Catalogue problem: {exc}", file=sys.stderr)
        return 2
    if args.only:
        wanted = {slug(s) for s in args.only}
        unknown = wanted - {d["subject"] for d in datasets}
        if unknown:
            print(f"Not in the catalogue: {sorted(unknown)}. Subjects available: "
                  f"{[d['subject'] for d in datasets]}", file=sys.stderr)
            return 2
        datasets = [d for d in datasets if d["subject"] in wanted]
    if not datasets:
        print(f"The catalogue {catalogue_path} has no series yet.", file=sys.stderr)
        return 2

    session = session or requests.Session()
    now = now or datetime.now().astimezone()
    print(f"Bank of Canada {'reprocessing from stored raw files' if args.from_raw else 'acquisition'}, {hub_core.iso(now)}")
    print(f"Hub folder: {args.repo_root}")

    failures = 0
    for entry in datasets:
        print(f"\nSeries {', '.join(entry['series_ids'])}")
        try:
            result = acquire_dataset(entry, args.repo_root, session, now, from_raw=args.from_raw)
        except AcquisitionError as exc:
            failures += 1
            print(f"  FAILED: {exc}")
            continue
        hub_core.print_result(result)

    print(f"\nDone: {len(datasets) - failures} succeeded, {failures} failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
