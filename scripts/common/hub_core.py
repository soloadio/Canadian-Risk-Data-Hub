"""Shared machinery for every acquisition script in the data hub.

A publisher's script (an "adapter", e.g. scripts/acquisition/statcan/download_statcan_table.py or
scripts/acquisition/boc/download_boc_series.py) only knows how to talk to its publisher and how to
turn the publisher's file into the hub's standard processed table. Everything that should behave
the same way for every publisher lives here:

    - HTTP with retries
    - stable dataset IDs (an existing metadata record pins the ID)
    - raw storage: saved byte for byte, a new dated version only when the data changed, never overwritten
    - retrieval history and checksums
    - the common quality checks, and refusing to overwrite outputs when a check fails
    - writing the processed file and the metadata record atomically

An adapter calls open_dataset() once it has the raw bytes and the dataset ID, runs its own
publisher-specific checks, builds the processed table, then calls finish_dataset().
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "validation"))
import hub_checks as checks  # noqa: E402  (lives in scripts/validation/)

TIMEOUT_SECONDS = 60
MAX_ATTEMPTS = 3


class AcquisitionError(Exception):
    """A problem with one dataset. The message is shown to the user."""


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


# ----------------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------------

def http(session, method: str, url: str, *, headers: dict, on_status=None, timeout: int = TIMEOUT_SECONDS,
         attempts: int = MAX_ATTEMPTS, **kwargs):
    """GET or POST with a timeout and a few retries on network or server errors.

    on_status(response) lets a publisher turn one of its own status codes into a clear message
    (for example Statistics Canada's 409 while a table is being updated) by raising AcquisitionError.
    """
    last_problem = "unknown problem"
    for attempt in range(1, attempts + 1):
        try:
            response = getattr(session, method)(url, timeout=timeout, headers=headers, **kwargs)
        except requests.RequestException as exc:
            last_problem = f"network error: {exc}"
        else:
            if on_status:
                on_status(response)
            if response.status_code >= 500:
                last_problem = f"HTTP {response.status_code}"
            elif response.status_code >= 400:
                raise AcquisitionError(f"HTTP {response.status_code} from {url}")
            else:
                return response
        if attempt < attempts:
            time.sleep(2 ** attempt)
    raise AcquisitionError(f"{last_problem} after {attempts} attempts ({url})")


# ----------------------------------------------------------------------------
# Dataset IDs, raw versions and metadata on disk
# ----------------------------------------------------------------------------

def resolve_dataset_id(metadata_dir: Path, prefix: str, derived_id: str, describe: str) -> tuple[str, str | None]:
    """Keep the ID a dataset already has, so a changed title or frequency never renames it.

    prefix is the part of the ID that identifies the publisher's table or series (e.g.
    'statcan_11100222_' or 'boc_v39079_'); describe names it in messages ('table 11-10-0222-01').
    Returns (dataset_id, note). The note explains when the derived ID was not used.
    """
    existing = sorted(p.stem for p in metadata_dir.glob(f"{prefix}*.json"))
    if len(existing) > 1:
        raise AcquisitionError(
            f"more than one metadata record for {describe}: {existing}. "
            "Keep the one in use and delete or archive the others."
        )
    if existing:
        if existing[0] != derived_id:
            return existing[0], (f"kept existing dataset ID {existing[0]} (the title, catalogue subject or "
                                 f"frequency now suggest {derived_id}; rename deliberately if needed)")
        return existing[0], None
    return derived_id, None


def read_previous_metadata(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise AcquisitionError(f"the existing metadata record {path} could not be read ({exc}); fix or remove it")


def previous_history(previous: dict) -> list[dict]:
    """Retrieval history on record. Records written before history existed get one entry from their fields."""
    if previous.get("retrieval_history"):
        return list(previous["retrieval_history"])
    if previous.get("checksum"):
        return [{
            "retrieved": previous.get("last_retrieved"),
            "checksum": previous.get("checksum"),
            "raw_file": previous.get("raw_file_location"),
            "source_last_updated": previous.get("source_last_updated"),
            "data_changed": None,
        }]
    return []


def sha256_of(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write to a temporary file then rename, so an interrupted run never leaves a half-written file."""
    temporary = path.with_name(path.name + ".partial")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def free_raw_path(raw_dir: Path, dataset_id: str, stamp: str, extension: str = ".csv") -> Path:
    """<id>_<date><ext>, or <id>_<date>_2<ext>, _3, ... if a file for that day already exists. Never overwrites."""
    candidate = raw_dir / f"{dataset_id}_{stamp}{extension}"
    counter = 2
    while candidate.exists():
        candidate = raw_dir / f"{dataset_id}_{stamp}_{counter}{extension}"
        counter += 1
    return candidate


def store_raw(root: Path, raw_dir: Path, dataset_id: str, stamp: str, raw_bytes: bytes, checksum: str,
              history: list[dict], extension: str = ".csv", companions: dict[str, bytes] | None = None) -> tuple[Path, bool]:
    """Save the raw data as a new version, unless the same bytes are already stored.

    companions are extra files received with the data, saved next to it as <raw file stem><suffix>
    (e.g. {"_cube_metadata.csv": ...}). Returns (raw file path, whether a new file was written).
    """
    if history:
        last = history[-1]
        last_file = root / last["raw_file"] if last.get("raw_file") else None
        if last.get("checksum") == checksum and last_file and last_file.exists():
            return last_file, False
    raw_path = free_raw_path(raw_dir, dataset_id, stamp, extension)
    atomic_write_bytes(raw_path, raw_bytes)  # saved exactly as received
    for suffix, data in (companions or {}).items():
        if data:
            atomic_write_bytes(raw_path.with_name(raw_path.stem + suffix), data)
    return raw_path, True


def script_location(script: str | Path, root: Path) -> str:
    """Path of a script relative to the hub folder (just the file name if it lives elsewhere)."""
    here = Path(script).resolve()
    try:
        return here.relative_to(root.resolve()).as_posix()
    except ValueError:
        return here.name


def quality_summary(rows_raw: int, rows_processed: int, coverage: str, results: list[dict], status: str) -> str:
    """One readable paragraph for the quality_notes field. The full results are under validation."""
    by_name = {r["check"]: r for r in results}
    parts = [
        f"{rows_raw} rows in raw file; {rows_processed} rows in processed file ({coverage}).",
        f"Missing values: {by_name['missing_values']['detail']}." if "missing_values" in by_name else "",
        f"Validation {status.replace('_', ' ')}.",
    ]
    warned = [f"{r['check']}: {r['detail']}" for r in results if r["status"] == checks.WARN and r["check"] != "missing_values"]
    if warned:
        parts.append("Warnings: " + " | ".join(warned))
    return " ".join(p for p in parts if p)


# ----------------------------------------------------------------------------
# One retrieval, end to end
# ----------------------------------------------------------------------------

@dataclass
class Dataset:
    """Where one retrieval of one dataset is stored, and what the hub already knew about it."""
    root: Path
    source_code: str
    dataset_id: str
    metadata_path: Path
    previous: dict
    history: list[dict]
    raw_dir: Path
    processed_dir: Path
    checksum: str
    previous_checksum: str | None
    raw_path: Path
    raw_written: bool
    notes: list[str] = field(default_factory=list)

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    @property
    def processed_path(self) -> Path:
        return self.processed_dir / f"{self.dataset_id}.csv"


def open_dataset(root: Path, source_code: str, dataset_id: str, stamp: str, raw_bytes: bytes,
                 raw_extension: str = ".csv", companions: dict[str, bytes] | None = None) -> Dataset:
    """Read what is on record for the dataset and keep the raw data as received (a new version only when it changed)."""
    metadata_dir = root / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = metadata_dir / f"{dataset_id}.json"
    previous = read_previous_metadata(metadata_path)
    history = previous_history(previous)

    raw_dir = root / "raw" / source_code / dataset_id
    processed_dir = root / "processed" / source_code / dataset_id
    raw_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    checksum = sha256_of(raw_bytes)
    previous_checksum = history[-1]["checksum"] if history else None
    raw_path, raw_written = store_raw(root, raw_dir, dataset_id, stamp, raw_bytes, checksum, history,
                                      raw_extension, companions)
    return Dataset(root, source_code, dataset_id, metadata_path, previous, history, raw_dir, processed_dir,
                   checksum, previous_checksum, raw_path, raw_written)


def finish_dataset(ds: Dataset, *, processed, frequency: str, results: list[dict], now: datetime,
                   record: dict, source_last_updated: str | None, schema: dict, schema_version: int,
                   rows_raw: int, coverage: str, validator: str, ai_use_note: str,
                   missing_status: dict[str, str] | None = None, update_frequency: str | None = None) -> dict:
    """Run the common checks, then write the processed file and the metadata record.

    results holds the adapter's own checks, already run. record holds the publisher-specific fields
    of the metadata record, in order (dataset_id through units); the retrieval, quality and history
    fields are added here so every publisher's record has the same shape.
    If any check fails, AcquisitionError is raised and the previous processed file and metadata stay as they were.
    """
    processed, processed_results = checks.check_processed(processed, frequency, missing_status)
    results = results + processed_results
    status = checks.overall_status(results)
    if status == "failed":
        raise AcquisitionError("validation failed, processed file and metadata left unchanged "
                               f"(raw file kept: {ds.relative(ds.raw_path)}):\n    " + "\n    ".join(checks.failures(results)))

    atomic_write_bytes(ds.processed_path, processed.to_csv(index=False, na_rep="").encode("utf-8"))

    changed = None if ds.previous_checksum is None else ds.previous_checksum != ds.checksum
    history = ds.history + [{
        "retrieved": iso(now),
        "checksum": ds.checksum,
        "raw_file": ds.relative(ds.raw_path),
        "source_last_updated": source_last_updated,
        "data_changed": changed,
        "schema_version": schema_version,
        "validation_status": status,
    }]
    metadata = dict(record)
    metadata.update({
        "last_retrieved": iso(now),
        "raw_file_location": ds.relative(ds.raw_path),
        "processed_file_location": ds.relative(ds.processed_path),
        "update_frequency": update_frequency,
        "source_last_updated": source_last_updated,
        "checksum": ds.checksum,
        "schema_version": schema_version,
        "schema": schema,
        "quality_notes": quality_summary(rows_raw, len(processed), coverage, results, status),
        "validation": {
            "status": status,
            "run_at": iso(now),
            "validator": validator,
            "checks": results,
        },
        "retrieval_history": history,
        "ai_use_note": ai_use_note,
    })
    atomic_write_bytes(ds.metadata_path, (json.dumps(metadata, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))

    return {
        "dataset_id": ds.dataset_id,
        "frequency": frequency,
        "rows_raw": rows_raw,
        "rows_processed": len(processed),
        "raw_path": ds.relative(ds.raw_path),
        "raw_written": ds.raw_written,
        "raw_megabytes": ds.raw_path.stat().st_size / 1e6,
        "changed": changed,
        "validation_status": status,
        "warnings": ds.notes + checks.warnings_of(results),
    }


def print_result(result: dict) -> None:
    """The SUCCESS block every acquisition script prints for one dataset."""
    change = {None: "first retrieval", True: "DATA CHANGED since last retrieval",
              False: "unchanged since last retrieval"}[result["changed"]]
    stored = "new raw file" if result["raw_written"] else "raw file already stored, not copied again"
    print(f"  SUCCESS: {result['dataset_id']} ({result['frequency']}), validation {result['validation_status'].replace('_', ' ')}")
    print(f"  {result['rows_raw']} raw rows ({result['raw_megabytes']:.1f} MB), {result['rows_processed']} processed rows, {change}")
    print(f"  raw file: {result['raw_path']} ({stored})")
    for warning in result["warnings"]:
        print(f"  warning: {warning}")
