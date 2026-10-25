#!/usr/bin/env python3
"""Check that the hub is ready for its next refresh, without downloading anything.

HOW TO USE
    python scripts/validation/check_refresh_readiness.py
    python scripts/validation/check_refresh_readiness.py --skip-checksums   (faster: does not re-hash raw files)

For every metadata record it checks that:
    - the raw file it points to exists and still has the recorded SHA-256 checksum (nobody edited it)
    - the processed file exists
    - the last validation did not fail
    - the last retrieval is recent enough for the dataset's frequency (otherwise it is due for a refresh)
and for every source catalogue that:
    - each catalogue entry has a metadata record (it has been acquired at least once)
    - each metadata record still has a catalogue entry (nothing is left behind after a catalogue change)

PROBLEM lines mean the next refresh would start from a broken state, and the exit code is 1.
NOTICE lines (due for a refresh, a record no longer in the catalogue) are for information.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "acquisition" / "statcan"))
sys.path.insert(0, str(SCRIPTS / "acquisition" / "boc"))

# How long after the last retrieval a dataset counts as due for a refresh, in days.
REFRESH_DUE_DAYS = {"daily": 3, "weekly": 10, "biweekly": 17, "monthly": 35, "quarterly": 100,
                    "semiannual": 190, "annual": 380}


def file_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def catalogue_prefixes(root: Path) -> dict[str, str]:
    """The dataset-ID prefix of every catalogue entry, e.g. {'statcan_11100222_': 'statcan table 11-10-0222-01'}."""
    import download_boc_series as boc
    import download_statcan_table as statcan

    prefixes = {}
    statcan_path = root / statcan.CATALOGUE_RELATIVE_PATH
    if statcan_path.exists():
        for table in statcan.load_catalogue(statcan_path):
            prefixes[f"statcan_{table['product_id']}_"] = f"statcan table {statcan.pretty_table_number(table['product_id'])}"
    boc_path = root / boc.CATALOGUE_RELATIVE_PATH
    if boc_path.exists():
        for entry in boc.load_catalogue(boc_path):
            prefixes[f"boc_{entry['id_part']}_"] = f"boc series {', '.join(entry['series_ids'])}"
    return prefixes


def check_record(metadata: dict, root: Path, now: datetime, verify_checksums: bool) -> tuple[list[str], list[str]]:
    """Returns (problems, notices) for one metadata record."""
    problems, notices = [], []
    raw = root / metadata.get("raw_file_location", "")
    if not metadata.get("raw_file_location") or not raw.is_file():
        problems.append(f"raw file missing: {metadata.get('raw_file_location')}")
    elif verify_checksums and file_checksum(raw) != metadata.get("checksum"):
        problems.append(f"raw file {metadata['raw_file_location']} no longer matches its recorded checksum "
                        "(raw files must never be edited)")
    processed = root / metadata.get("processed_file_location", "")
    if not metadata.get("processed_file_location") or not processed.is_file():
        problems.append(f"processed file missing: {metadata.get('processed_file_location')}")
    status = (metadata.get("validation") or {}).get("status")
    if status == "failed":
        problems.append("the last validation failed")
    elif status is None:
        problems.append("no validation results on record")

    limit = REFRESH_DUE_DAYS.get(metadata.get("frequency", ""))
    try:
        retrieved = datetime.fromisoformat(metadata["last_retrieved"])
    except (KeyError, TypeError, ValueError):
        problems.append(f"last_retrieved is missing or not a timestamp: {metadata.get('last_retrieved')!r}")
    else:
        age = (now - retrieved).days
        if limit is not None and age > limit:
            notices.append(f"due for a refresh: last retrieved {age} days ago ({metadata.get('frequency')} data, "
                           f"refresh at least every {limit} days)")
    return problems, notices


def main(argv=None, now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check that every dataset is ready for its next refresh (offline).")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2],
                        help="Hub root folder (default: two levels above this script).")
    parser.add_argument("--skip-checksums", action="store_true", help="Do not re-hash raw files (faster).")
    args = parser.parse_args(argv)
    now = now or datetime.now().astimezone()

    try:
        prefixes = catalogue_prefixes(args.repo_root)
    except ValueError as exc:
        print(f"PROBLEM  catalogue: {exc}")
        return 1
    records = sorted((args.repo_root / "metadata").glob("*.json"))
    total_problems = 0

    for path in records:
        try:
            metadata = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            print(f"\n{path.stem}\n  PROBLEM  metadata record cannot be read ({exc})")
            total_problems += 1
            continue
        problems, notices = check_record(metadata, args.repo_root, now, not args.skip_checksums)
        if not any(path.stem.startswith(p) for p in prefixes):
            notices.append("not in any source catalogue (left over from a catalogue change?)")
        print(f"\n{path.stem}")
        for line in problems:
            print(f"  PROBLEM  {line}")
        for line in notices:
            print(f"  NOTICE   {line}")
        if not problems and not notices:
            print("  OK       raw and processed files present, checksum matches, validation "
                  f"{metadata['validation']['status'].replace('_', ' ')}")
        total_problems += len(problems)

    never_acquired = [label for prefix, label in prefixes.items()
                      if not any(p.stem.startswith(prefix) for p in records)]
    if never_acquired:
        print("\nCatalogue entries with no metadata record yet (run the acquisition script):")
        for label in never_acquired:
            print(f"  PROBLEM  {label}")
        total_problems += len(never_acquired)

    print(f"\n{len(records)} datasets checked: {total_problems} problem(s).")
    return 1 if total_problems else 0


if __name__ == "__main__":
    sys.exit(main())
