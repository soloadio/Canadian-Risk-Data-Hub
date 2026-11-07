#!/usr/bin/env python3
"""Re-run the quality checks on processed files already on disk, for any publisher.

HOW TO USE
    Check one dataset:
        python scripts/validation/validate_dataset.py boc_v39079_policy_rate_daily
    Check every dataset that has a metadata record:
        python scripts/validation/validate_dataset.py --all

Nothing is downloaded and no file is changed. The acquisition scripts run the same checks on
every download and record the results in each metadata record under "validation".
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hub_checks as checks  # noqa: E402
import rai_checks  # noqa: E402


def source_of(dataset_id: str) -> str:
    """The publisher code is the first part of every dataset ID (statcan_..., boc_...)."""
    return dataset_id.split("_", 1)[0]


def missing_status_for(source: str) -> dict[str, str]:
    """Status codes that explain a missing value, for publishers that use them."""
    if source == "statcan":
        import validate_statcan_dataset  # imported here to avoid a circular import
        return validate_statcan_dataset.MISSING_STATUS
    return {}


def validate_dataset(metadata_path: Path, root: Path, now: datetime | None = None) -> tuple[str, list[dict]]:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    processed_path = root / metadata["processed_file_location"]
    if not processed_path.exists():
        return "skipped", [checks.check("processed_file", checks.WARN,
                                        f"{processed_path} not found; run the acquisition script first")]
    frequency = metadata.get("frequency", "")
    now = now or datetime.now().astimezone()
    processed, results = checks.check_processed(checks.read_processed(processed_path), frequency,
                                                missing_status_for(source_of(metadata["dataset_id"])))
    results.append(checks.check_freshness(metadata.get("observation_end"), frequency, now))
    # The team standard's add-on checks, as the acquisition scripts run them.
    results += rai_checks.run_rai_checks(processed, metadata, now)
    return checks.overall_status(results), results


def main(argv=None, sources: list[str] | None = None) -> int:
    scope = " and ".join(sources) if sources else "all publishers"
    parser = argparse.ArgumentParser(description=f"Re-run the quality checks on processed files on disk ({scope}).")
    parser.add_argument("dataset_id", nargs="*", help="Dataset ID(s), e.g. statcan_11100222_household_spending_annual")
    parser.add_argument("--all", action="store_true", help="Check every metadata record")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2],
                        help="Hub root folder (default: two levels above this script).")
    args = parser.parse_args(argv)

    metadata_dir = args.repo_root / "metadata"
    if args.all:
        paths = sorted(metadata_dir.glob("*.json"))
        if sources:
            paths = [p for p in paths if source_of(p.stem) in sources]
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
            print(f"  {rai_checks.SEVERITY.get(r['status'], r['status']):7}  {r['check']}: {r['detail']}")
        print(f"  => {status}")
        failed += status == "failed"
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
