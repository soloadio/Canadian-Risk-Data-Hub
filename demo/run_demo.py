#!/usr/bin/env python3
"""Run the whole hub end to end on small sample files and check the results, in a few seconds, offline.

HOW TO USE (from the repository root, after `pip install -r requirements.txt`)
    python demo/run_demo.py

It builds a separate demo hub in demo/output/ (deleted and recreated on every run; your real data is not
touched) and runs every stage on the sample files in demo/sample/, which are small cuts of real Statistics
Canada and Bank of Canada files:

    1. acquisition, day 1   both publishers' scripts, with the sample files served in place of the
                            publishers' services (nothing is downloaded)
    2. acquisition, day 2   the same files again a day later: raw files are reused, not copied, and the
                            reproducibility rating becomes Confirmed High (identical data on two days)
    3. validation           every processed file re-checked (scripts/validation/validate_dataset.py)
    4. refresh readiness    raw checksums, files and catalogues (scripts/validation/check_refresh_readiness.py)
    5. analysis-ready       the four tables built and validated (scripts/processing/build_analysis_ready.py)
    6. promotion readiness  what each dataset and table still needs before sign-off
    7. comparison           every processed file, metadata record and analysis-ready table compared byte
                            for byte with demo/expected/

The demo passes when every stage succeeds and every output matches. The dates are fixed (2026-11-07 and
2026-11-08), so the outputs are the same on every computer.

    python demo/run_demo.py --update-expected   regenerate demo/expected/ after an intended change
"""
from __future__ import annotations

import argparse
import contextlib
import filecmp
import io
import json
import shutil
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo"
SCRIPTS = ROOT / "scripts"
for folder in ["acquisition/statcan", "acquisition/boc", "validation", "processing"]:
    sys.path.insert(0, str(SCRIPTS / folder))

import build_analysis_ready  # noqa: E402
import check_promotion_readiness  # noqa: E402
import check_refresh_readiness  # noqa: E402
import download_boc_series  # noqa: E402
import download_statcan_table  # noqa: E402
import validate_dataset  # noqa: E402

DAY_1 = datetime(2026, 11, 7, 12, 0, tzinfo=timezone(timedelta(hours=-5)))
DAY_2 = DAY_1 + timedelta(days=1)
COMPARED = ["processed", "metadata", "analysis_ready"]


# ----------------------------------------------------------------------------
# The publishers, replayed from the sample files
# ----------------------------------------------------------------------------

class Reply:
    def __init__(self, status_code=200, payload=None, content=b""):
        self.status_code, self._payload = status_code, payload
        self.content = content if payload is None else json.dumps(payload).encode("utf-8")

    def json(self):
        return self._payload

    def iter_content(self, chunk_size):
        yield self.content


class SampleServices:
    """Answers the calls the acquisition scripts make, from demo/sample/, the way the real services do."""

    def __init__(self, sample: Path):
        self.sample = sample
        self.valet = {}
        for path in sample.glob("boc_*.json"):
            content = path.read_bytes()
            self.valet[frozenset(json.loads(content)["seriesDetail"])] = content

    def get(self, url, **kwargs):
        if "/getFullTableDownloadCSV/" in url:  # Statistics Canada: where is the zip?
            pid = url.split("/getFullTableDownloadCSV/")[1].split("/")[0]
            return Reply(payload={"status": "SUCCESS", "object": f"sample://statcan/{pid}.zip"})
        if url.startswith("sample://statcan/"):  # Statistics Canada: the zip itself
            pid = url.rsplit("/", 1)[1].removesuffix(".zip")
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr(f"{pid}.csv", (self.sample / f"statcan_{pid}.csv").read_bytes())
            return Reply(content=archive.getvalue())
        if "/valet/observations/" in url:  # Bank of Canada
            ids = frozenset(url.split("/valet/observations/")[1].split("/")[0].split(","))
            if ids not in self.valet:
                return Reply(404, {"message": f"Series {sorted(ids)} not in the demo samples."})
            return Reply(content=self.valet[ids])
        return Reply(404, {"message": f"no sample for {url}"})

    def post(self, url, json=None, **kwargs):  # Statistics Canada getCubeMetadata
        pid = str(json[0]["productId"])
        cube = (self.sample / f"statcan_{pid}_cube.json").read_text(encoding="utf-8")
        return Reply(payload=[{"status": "SUCCESS", "object": __import__("json").loads(cube)}])


# ----------------------------------------------------------------------------
# The run
# ----------------------------------------------------------------------------

def step(title: str, run, verbose: bool) -> tuple[int, str]:
    """Run one stage, capturing its output. Returns (exit code, output)."""
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        code = run()
    text = output.getvalue()
    print(f"{'OK  ' if code == 0 else 'FAIL'}  {title}")
    if verbose or code != 0:
        print("      " + text.strip().replace("\n", "\n      "))
    return code, text


def fresh_hub(out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    (out / "sources").mkdir(parents=True)
    for catalogue in (DEMO / "sources").glob("*.csv"):
        shutil.copy(catalogue, out / "sources" / catalogue.name)


def compare(out: Path, expected: Path) -> list[str]:
    """Paths that differ from the expected outputs (missing, extra or different bytes)."""
    produced = {p.relative_to(out).as_posix() for d in COMPARED for p in (out / d).rglob("*") if p.is_file()}
    wanted = {p.relative_to(expected).as_posix() for d in COMPARED for p in (expected / d).rglob("*") if p.is_file()}
    problems = [f"missing: {p}" for p in sorted(wanted - produced)] + [f"unexpected: {p}" for p in sorted(produced - wanted)]
    problems += [f"different: {p}" for p in sorted(produced & wanted)
                 if not filecmp.cmp(out / p, expected / p, shallow=False)]
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the hub end to end on the demo samples and check the results.")
    parser.add_argument("--output", type=Path, default=DEMO / "output", help="Where to build the demo hub "
                        "(default demo/output; deleted and recreated).")
    parser.add_argument("--update-expected", action="store_true", help="Replace demo/expected/ with this run's outputs.")
    parser.add_argument("--verbose", action="store_true", help="Print every stage's full output.")
    args = parser.parse_args(argv)
    out, expected = args.output, DEMO / "expected"

    print(f"Canadian Risk Data Hub demo: building a demo hub in {out}\n")
    fresh_hub(out)
    services = SampleServices(DEMO / "sample")
    hub = ["--repo-root", str(out)]
    stages = [
        ("1. acquisition, day 1: Statistics Canada", lambda: download_statcan_table.main(hub, session=services, now=DAY_1)),
        ("1. acquisition, day 1: Bank of Canada", lambda: download_boc_series.main(hub, session=services, now=DAY_1)),
        ("2. acquisition, day 2: Statistics Canada (same data: raw reused)",
         lambda: download_statcan_table.main(hub, session=services, now=DAY_2)),
        ("2. acquisition, day 2: Bank of Canada (same data: raw reused)",
         lambda: download_boc_series.main(hub, session=services, now=DAY_2)),
        ("3. validation of every processed file", lambda: validate_dataset.main(["--all", *hub])),
        ("4. refresh readiness", lambda: check_refresh_readiness.main(hub, now=DAY_2)),
        ("5. analysis-ready tables built and validated", lambda: build_analysis_ready.main(hub, now=DAY_2)),
        ("6. promotion readiness", lambda: check_promotion_readiness.main(hub)),
    ]
    failed = 0
    outputs = {}
    for title, run in stages:
        code, text = step(title, run, args.verbose)
        failed += code != 0
        outputs[title] = text

    if args.update_expected:
        if failed:
            print("\nNot updating demo/expected/: a stage failed.")
            return 1
        if expected.exists():
            shutil.rmtree(expected)
        for d in COMPARED:
            shutil.copytree(out / d, expected / d)
        print("\ndemo/expected/ updated from this run. Review the changes with git diff before committing.")
        return 0

    problems = compare(out, expected)
    print(f"{'OK  ' if not problems else 'FAIL'}  7. outputs compared with demo/expected/")
    for line in problems[:20]:
        print(f"      {line}")

    # A short account of what the demo showed.
    datasets = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted((out / "metadata").glob("*.json"))}
    print("\nDatasets (metadata/):")
    for dataset_id, record in datasets.items():
        print(f"  {dataset_id}: validation {record['validation']['status'].replace('_', ' ')}, "
              f"{len(record['retrieval_history'])} retrievals, {record['reproducibility_rating']['rating']}")
    tables = sorted((out / "metadata" / "analysis_ready").glob("*.json"))
    print("Analysis-ready tables (analysis_ready/):")
    for path in tables:
        record = json.loads(path.read_text(encoding="utf-8"))
        provenance = next(c for c in record["validation"]["checks"] if c["check"] == "provenance")
        print(f"  {record['table_id']}: {record['rows']} rows, validation {record['validation']['status']}; "
              f"{provenance['detail']}")
    summary = [line for line in outputs["6. promotion readiness"].splitlines() if line.endswith("held.")]
    if summary:
        print(f"Promotion: {summary[-1]} (sign-off needs a reviewer; see documentation/promotion_log.csv)")

    ok = not failed and not problems
    print(f"\nDEMO {'PASSED' if ok else 'FAILED'}: {len(stages) - failed} of {len(stages)} stages succeeded; "
          f"{'all outputs match demo/expected/' if not problems else f'{len(problems)} outputs differ from demo/expected/'}.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
