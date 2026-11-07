#!/usr/bin/env python3
"""Which datasets and analysis-ready tables may be promoted, and what is still open for each.

HOW TO USE
    python scripts/validation/check_promotion_readiness.py                 report only
    python scripts/validation/check_promotion_readiness.py --write-qa-log  also rewrite documentation/qa_log.csv

This follows the team's promotion and sign-off workflow (RAI_Promotion_and_Signoff_Workflow, Ayesha Malhi,
W3-AYE-04). A dataset is promoted when it meets every criterion and a reviewer other than the preparer has
signed it off in the promotion log (documentation/promotion_log.csv) for the raw data now on record.

THE CRITERIA (numbers as in the workflow)
    1  the latest validation is not 'failed'                                          checked here
    2  every Required metadata key is filled; description is more than the name       checked here
    3  every WARNING has a disposition: Accept (reason), Fix or Waive (approver)       checked here, from the log
    4  manual checks done where triggered (outliers, metadata vs source, revisions)    reviewer, listed here
    5  a person has reviewed the licence                                               reviewer
    6  reproducibility_rating is Confirmed High, or Potential High with a reviewer note checked here / reviewer
    7  known_limitations records every accepted warning that affects use               reviewer
    8  analysis-ready tables keep dataset_id, unit and status; derived series documented checked here (tables)
    9  a reviewer other than the preparer signed the log for the data reviewed          checked here, from the log

THE PROMOTION LOG (one row per review; the latest row for a dataset counts)
    dataset_id, raw_checksum_reviewed, validation_status, warnings_total, dispositions, preparer, reviewer,
    review_date, decision (promoted / held / waived), approver, notes
    dispositions: one entry per warning, separated by ' | ', each '<check>: <Accept|Fix|Waive> - <reason>'.
    For an analysis-ready table, dataset_id is the table name and raw_checksum_reviewed is the table's
    content_checksum from its record. A sign-off stands while that checksum is unchanged.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hub_checks as checks  # noqa: E402
import rai_checks  # noqa: E402

LOG_RELATIVE_PATH = Path("documentation") / "promotion_log.csv"
QA_LOG_RELATIVE_PATH = Path("documentation") / "qa_log.csv"
LOG_FIELDS = ["dataset_id", "raw_checksum_reviewed", "validation_status", "warnings_total", "dispositions", "preparer",
              "reviewer", "review_date", "decision", "approver", "notes"]
QA_LOG_FIELDS = ["log_id", "dataset_id", "check_id", "check_performed", "result", "severity", "date", "performed_by",
                 "method", "corrective_action_or_note", "reviewer", "status"]
DECISIONS = {"Accept", "Fix", "Waive"}


def read_log(path: Path) -> dict[str, dict]:
    """The latest log row for each dataset or table."""
    if not path.exists():
        return {}
    latest = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            row = {k: (v or "").strip() for k, v in row.items()}
            if row.get("dataset_id"):
                latest[row["dataset_id"]] = row
    return latest


def parse_dispositions(text: str) -> dict[str, tuple[str, str]]:
    """'freshness: Accept - survey is biennial | date_gaps: Accept - ...' -> {check: (decision, reason)}."""
    out = {}
    for part in [p.strip() for p in text.split(" | ") if p.strip()]:
        check_name, _, rest = part.partition(":")
        decision, _, reason = rest.strip().partition(" - ")
        out[check_name.strip()] = (decision.strip(), reason.strip())
    return out


def assess(item_id: str, checksum: str | None, validation: dict, log_row: dict | None, extra_open: list[str],
           reviewer_items: list[str]) -> dict:
    """Shared part of the assessment: criteria 1, 3 and 9, and the overall decision."""
    status = validation.get("status")
    results = validation.get("checks", [])
    blocking, open_items = [], list(extra_open)

    if status == "failed":                                                   # criterion 1
        blocking.append("1: the latest validation failed")
    current_review = log_row if log_row and log_row.get("raw_checksum_reviewed") == checksum else None
    if log_row and not current_review:
        open_items.append("the data changed since the last review (checksum differs): review again")

    warnings = [r["check"] for r in results if r["status"] == checks.WARN]   # criterion 3
    dispositions = parse_dispositions(current_review.get("dispositions", "")) if current_review else {}
    without = [w for w in warnings if w not in dispositions or dispositions[w][0] not in DECISIONS]
    if without:
        open_items.append(f"3: warnings without a disposition: {without}")
    waived = [w for w in warnings if dispositions.get(w, ("",))[0] == "Waive"]
    if waived and not (current_review or {}).get("approver"):
        open_items.append(f"3: waived warnings need an approver: {waived}")

    signed = bool(current_review and current_review.get("decision") == "promoted" and current_review.get("reviewer")
                  and current_review.get("reviewer") != current_review.get("preparer"))
    if blocking:
        decision = "held"
    elif signed and not open_items:
        decision = "promoted"
    elif not open_items:
        decision = "ready for sign-off"
    else:
        decision = "held"
    if not signed:                                                           # criterion 9
        reviewer_items = reviewer_items + ["9: a reviewer other than the preparer signs the promotion log"]
    return {"id": item_id, "decision": decision, "blocking": blocking, "open": open_items,
            "reviewer": [] if signed else reviewer_items, "warnings": len(warnings), "signed": signed}


def assess_dataset(record: dict, log_row: dict | None) -> dict:
    validation = record.get("validation") or {}
    by_check = {r["check"]: r for r in validation.get("checks", [])}
    open_items, reviewer_items = [], []
    completeness = by_check.get("metadata_completeness") or rai_checks.check_metadata_completeness(record)
    if completeness["status"] in (checks.WARN, checks.FAIL):                 # criterion 2
        open_items.append(f"2: {completeness['detail']}")
    if (by_check.get("period_change") or {}).get("flagged"):                 # criterion 4
        reviewer_items.append(f"4: review the {by_check['period_change']['flagged']} flagged period changes (QA-OUT2)")
    if (by_check.get("revisions") or {}).get("status") == checks.INFO:
        reviewer_items.append("4: judge whether the revisions found are expected (QA-RV2)")
    reviewer_items.append("4: compare the metadata with the source documentation (QA-PRV3)")
    reviewer_items.append(f"5: review the licence ({(record.get('licence') or {}).get('name', 'not recorded')})")
    rating = (record.get("reproducibility_rating") or {}).get("rating")
    if rating != "Confirmed High":                                           # criterion 6
        reviewer_items.append(f"6: reproducibility is {rating}: add a reviewer note, or retrieve again on another day")
    reviewer_items.append("7: check known_limitations covers every accepted warning")
    return assess(record["dataset_id"], record.get("checksum"), validation, log_row, open_items, reviewer_items)


def assess_table(record: dict, log_row: dict | None, dataset_decisions: dict[str, str]) -> dict:
    validation = record.get("validation") or {}
    by_check = {r["check"]: r for r in validation.get("checks", [])}
    open_items = []
    for name in ["table_layout", "derivations_documented"]:                  # criterion 8
        if (by_check.get(name) or {}).get("status") not in (checks.PASS, checks.INFO, None):
            open_items.append(f"8: {name}: {by_check[name]['detail']}")
    not_promoted = [i["dataset_id"] for i in record.get("inputs", []) if dataset_decisions.get(i["dataset_id"]) != "promoted"]
    if not_promoted:
        open_items.append(f"inputs not yet promoted: {not_promoted}")
    reviewer_items = ["4: spot-check the table against its inputs and its documented selection"]
    return assess(record["table_id"], record.get("content_checksum"), validation, log_row, open_items, reviewer_items)


def qa_log_rows(item_id: str, validation: dict, log_row: dict | None) -> list[dict]:
    date = (validation.get("run_at") or "")[:10]
    rows = rai_checks.to_log_rows(item_id, validation.get("checks", []), date)
    dispositions = parse_dispositions(log_row.get("dispositions", "")) if log_row else {}
    for row in rows:
        if row["check_performed"] in dispositions:
            decision, reason = dispositions[row["check_performed"]]
            row["corrective_action_or_note"] = f"{decision}: {reason}"
            row["reviewer"] = (log_row or {}).get("reviewer", "")
            row["status"] = "Closed" if (log_row or {}).get("decision") == "promoted" else "In review"
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Report which datasets and analysis-ready tables may be promoted.")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2],
                        help="Hub root folder (default: two levels above this script).")
    parser.add_argument("--write-qa-log", action="store_true", help=f"Rewrite {QA_LOG_RELATIVE_PATH.as_posix()} "
                        "from the latest validation results, in the team standard's QA log layout.")
    args = parser.parse_args(argv)

    log = read_log(args.repo_root / LOG_RELATIVE_PATH)
    datasets = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((args.repo_root / "metadata").glob("*.json"))]
    tables = [json.loads(p.read_text(encoding="utf-8"))
              for p in sorted((args.repo_root / "metadata" / "analysis_ready").glob("*.json"))]

    assessments = [assess_dataset(d, log.get(d["dataset_id"])) for d in datasets]
    decisions = {a["id"]: a["decision"] for a in assessments}
    assessments += [assess_table(t, log.get(t["table_id"]), decisions) for t in tables]

    for a in assessments:
        print(f"\n{a['id']}\n  {a['decision'].upper()}  ({a['warnings']} warnings)")
        for line in a["blocking"]:
            print(f"  BLOCKING  {line}")
        for line in a["open"]:
            print(f"  OPEN      {line}")
        for line in a["reviewer"]:
            print(f"  REVIEWER  {line}")
    counts = {d: sum(a["decision"] == d for a in assessments) for d in ["promoted", "ready for sign-off", "held"]}
    print(f"\n{len(assessments)} items: {counts['promoted']} promoted, {counts['ready for sign-off']} ready for sign-off, "
          f"{counts['held']} held.")

    if args.write_qa_log:
        rows = []
        for d in datasets:
            rows += qa_log_rows(d["dataset_id"], d.get("validation") or {}, log.get(d["dataset_id"]))
        for t in tables:
            rows += qa_log_rows(t["table_id"], t.get("validation") or {}, log.get(t["table_id"]))
        path = args.repo_root / QA_LOG_RELATIVE_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=QA_LOG_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        print(f"QA log written: {QA_LOG_RELATIVE_PATH.as_posix()} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
