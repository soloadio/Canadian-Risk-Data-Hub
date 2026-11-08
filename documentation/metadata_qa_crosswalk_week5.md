# Week 5 crosswalk: hub implementation vs. the team metadata and QA standard (W5-SOL-01)

**Date:** 2026-11-07 · **Author:** Solomon Ador-Dionisio · **For:** Ayesha Malhi, Maurya Shah, Aahan Patel, Ahmad Samir, Dr. James Hurst (RAI)

This note maps the hub, as committed on 2026-11-07, against Ayesha Malhi's metadata and QA standard. For each field and check it says whether the hub now follows the standard, how, and what is still open. The aim is a single standard for the hub and the team, not two.

## 1. Inputs

| Document | Author | Used | Status |
|---|---|---|---|
| RAI_Metadata_QA_Standard_v1.0.xlsx: dataset metadata, column dictionary, conventions, 30-check QA checklist, comparative QA, promotion, integration review (W3-AYE-01 to 05) | Ayesha Malhi | In full | Read from the link in the RAI tracker |
| RAI_Promotion_and_Signoff_Workflow.docx (W3-AYE-04) | Ayesha Malhi | In full | Read |
| RAI_Integration_Review_W3.docx (W3-AYE-05) | Ayesha Malhi | In full: its 14 findings are answered in section 6 | Read |
| tests/test_rai_checks.py (W3-AYE-02) | Ayesha Malhi | Added to the repository unchanged; it is the contract for the add-on checks | Read; all 12 tests pass |
| scripts/validation/rai_checks.py, rai_compare.py (W3-AYE-02) | Ayesha Malhi | **Not available:** the Drive folder they are in is not shared with this account. The hub's `rai_checks.py` was written from the standard and her tests instead | To reconcile when shared (section 7) |
| W5-AYE-01 to 04 (Week 5 crosswalk, QA run, analysis-ready review, exception register) | Ayesha Malhi | **Not available:** marked Completed in the tracker, but no links | To compare when shared (section 7) |

## 2. Dataset-level metadata (sheet 1_Dataset_Metadata)

The standard was written against the hub's 36 record keys, and every record still has exactly those 36 keys, in the same order. Changes this week:

| Key | Standard | Hub before | Hub now |
|---|---|---|---|
| `description` | Required; must not simply repeat `dataset_name` | StatCan records repeated the table title | From the catalogue's new `description` column (one or two sentences on what is measured and why the hub keeps it). Checked by `metadata_completeness` |
| `revision_policy` | Recommended; one of `none`, `scheduled`, `ad_hoc`, `unknown` | Free text (BoC); empty (StatCan) | Codes: policy rate and USD/CAD `none`, CORRA `ad_hoc`, yields `unknown`, Labour Force `scheduled`, household spending `unknown`. The explanations moved to `known_limitations`. Checked by `metadata_completeness` |
| `priority` | Recommended | Empty for StatCan | `high` for both StatCan tables, by the rule already used for the Bank of Canada datasets: the tracker priority of the task that brought the dataset into the hub (W1-SOL-02, High). The team can change it in the catalogue. Checked by `metadata_completeness` |
| `primary_key` | Required; columns that identify an observation | `date, geography, series` | `date, geography, series_id`, the stable key (section 3) |
| `reproducibility_rating` | Required; Confirmed High needs identical data on two **different days** | Two identical retrievals, any day | The standard's rule. StatCan datasets: Confirmed High (household spending identical on 2026-10-10, 10-18, 10-24 and 11-07; Labour Force on 10-10, 10-18 and 10-24). BoC datasets: Potential High (section 6, finding 9) |
| `known_limitations` | Required; "none known" if none | Filled for all six | Extended: StatCan classification change and hierarchy, Labour Force revision schedule, revision details for BoC |
| `validation` | Required | pass / warn / fail | Adds INFO, the add-on checks, the name of the standard (`standard`) and whether the run was a retrieval or a reprocessing (`mode`) |

Owner, reviewer and sign-off are not record keys, as the standard specifies; they are in `documentation/promotion_log.csv` (section 5).

## 3. Column dictionary (sheet 2_Column_Dictionary)

| Column | Standard | Hub now |
|---|---|---|
| `date`, `geography`, `series`, `value`, `unit`, `source`, `dataset_id`, `status` | Required | Unchanged |
| `geo_code` | Recommended: official geography code (StatCan DGUID) | **Added.** StatCan DGUID; empty for the Bank of Canada, which publishes no code |
| `series_id` | Recommended: the publisher's stable series ID (StatCan VECTOR; BoC series code) | **Added.** StatCan vector (e.g. `v2062815`), Valet series name (e.g. `BD.CDN.10YR.DQ.YLD`). New check `series_identity` fails if an ID has two labels or a label two IDs. The revisions check matches on it, so a relabelled series is reported as relabelled, not as removed and re-added |
| status category | Status symbol table (sheet 3): symbol, meaning, category | **Added as `status_category`** (`not_available`, `not_applicable`, `suppressed`, `unreliable`, `quality_flag`, `rounded_zero`, `below_detection`; `unknown` for a symbol the standard does not list). The publisher's own symbol stays in `status`, as the Week 5 task asks ("preserve provider-specific status while adding standardized RAI fields") |
| dimension columns | Optional: each dimension in its own column | Not adopted yet: the `series` label plus `series_id` identify every series, and a split layout differs per table. Can be added per analysis-ready table where a dimension is needed (e.g. `age_group`, `category`) |

Processed files therefore have 11 columns. All six datasets were rebuilt in this layout. The Labour Force table (1.2 GB) was rebuilt from its stored raw file, with no download; the new `--from-raw` option verifies the stored file's checksum first.

## 4. Conventions (sheet 3_Conventions)

| Convention | Standard's status | Hub |
|---|---|---|
| Dataset ID, dates (first day of period), percentages as points, timestamps with offset, missing values blank plus status, variable names, numbers and units, raw never edited, AI use | Confirmed / Adopted | Already in the hub; unchanged |
| Severity: pass/warn/fail = PASS/WARNING/FAIL, plus INFO | Adopted | **INFO added.** Terminated series, missing values all explained by a status, revisions, and period changes below 1% are INFO |
| Series identity: label for reading, `series_id` for joins | Proposed | **Adopted** (section 3) |
| Geography: name plus official code | Proposed | **Adopted** for StatCan (`geo_code`). A shared province lookup (name, SGC code, abbreviation) is still open |
| Reproducibility on different days | Proposed | **Adopted** (section 2) |
| Derived series labelled and documented | Proposed | **Adopted.** Derived rows have a `derivation` column, "(derived)" in the series name, and the rule in the table's record; the validator recomputes them |
| Sign-off in a promotion log keyed to the raw checksum | Proposed | **Adopted** (section 5) |
| Licence reviewed by a person before ingestion | Proposed | A reviewer item in promotion readiness (criterion 5); not yet recorded separately |
| Text used for missing, zero versus not quantified, categories, event tables | Adopted / Proposed (Ontario test) | Checks available in `rai_checks.py` for event tables; no event table is in the hub yet |

## 5. QA checklist (sheet 4_QA_Checklist): the 30 checks

| Check ID | Check | Where it runs now | Severity |
|---|---|---|---|
| QA-ACQ1 | Download completed (retries) | `hub_core.http` | FAIL |
| QA-ACQ2 | File non-empty; row count matches the publisher | `row_count` (StatCan), `series_present` (BoC), `processed_layout` | FAIL / WARNING |
| QA-ACQ3 | File opens in the expected format | adapters (`read_raw`, `read_payload`) | FAIL |
| QA-ACQ4 | Checksum recorded; raw file unchanged | `checksum`, `check_refresh_readiness.py`, `--from-raw` | WARNING |
| QA-SCH1 | Expected columns present | `processed_layout`; `table_layout` for analysis-ready tables | FAIL |
| QA-SCH2 | No unexpected new or renamed columns | `schema`, `schema_version` | WARNING |
| QA-SCH3 | Types match | `dates`, `numeric_values` | FAIL / WARNING |
| QA-CMP1 | Key fields never empty | `key_fields` (add-on) | FAIL |
| QA-CMP2 | Missing share within 5% | `missing_share` (add-on), `missing_values`, `nulls_explained` | INFO / WARNING |
| QA-CMP3 | Expected geographies and series present | `geography`, `series_present` | FAIL |
| QA-DUP1 | No duplicate keys | `duplicates`, `series_identity`, `table_key` | FAIL |
| QA-DUP2 | No duplicate rows | `duplicates` (exact copies), `duplicate_rows` | WARNING |
| QA-VAL1 | Values within plausible range; zero not used for "not quantified" | `value_range` (add-on); `zero_as_missing` for event tables | WARNING |
| QA-VAL2 | Percentage convention | `percent_convention` (add-on). Standard errors are excluded: they are legitimately below 1 point and made 189 Labour Force series a false alarm | WARNING |
| QA-VAL3 | Categories, status symbols and dates valid | `status_symbols` (add-on), `status_category` | INFO / WARNING |
| QA-TS1 | No missing periods | `date_gaps` | WARNING |
| QA-TS2 | No future dates | `future_dates` (add-on; now for every publisher, replacing the Bank of Canada's own) | FAIL |
| QA-TS3 | Not stale | `freshness` | WARNING |
| QA-TS4 | Large period-over-period changes flagged | `period_change` (add-on) | INFO / WARNING |
| QA-XF1 | Totals equal components | **Not yet automated:** needs each table's category hierarchy | - |
| QA-XF2 | One unit per series | `unit_consistency` (add-on), `units` (BoC) | FAIL / WARNING |
| QA-OUT1 | Outliers listed for review | `period_change` lists them | INFO |
| QA-OUT2 | Judge whether an outlier is an event, revision or error | Manual: reviewer item in promotion readiness | - |
| QA-PRV1 | Required metadata keys complete | `metadata_completeness` (add-on) | WARNING |
| QA-PRV2 | Table agrees with its metadata | `metadata_consistency` (add-on); `provenance` and `inputs_current` for analysis-ready tables | WARNING / FAIL |
| QA-PRV3 | Metadata agrees with the source documentation | Manual: reviewer item | - |
| QA-RV1 | History compared with the prior retrieval | `revisions` (now matched on `series_id`) | INFO |
| QA-RV2 | Judge whether revisions are expected | Manual: reviewer item when revisions are found | - |
| QA-RV3 | Summarize release-to-release changes | Manual | - |
| QA-NAR1 | Analyst QA narrative | Manual | - |

Coverage: 24 automated (13 that were in the hub, plus the 11 add-on checks, now part of every acquisition run), 5 manual, 1 not yet automated. These are the same counts as the standard. Every result is written to `documentation/qa_log.csv` in the standard's QA log layout (`log_id`, `dataset_id`, `check_id`, `check_performed`, `result`, `severity`, `date`, `performed_by`, `method`, `corrective_action_or_note`, `reviewer`, `status`) by `check_promotion_readiness.py --write-qa-log`. The 2026-11-07 log has 182 rows.

**Promotion (sheet 6 and the workflow).** `scripts/validation/check_promotion_readiness.py` applies the nine criteria. It checks automatically:
- the latest validation did not fail (criterion 1);
- the Required keys are filled (2);
- every warning has a disposition (3);
- reproducibility (6);
- analysis-ready tables keep dataset_id, unit and status and document their derived series (8);
- a reviewer other than the preparer signed for the current checksum (9).

It lists the manual items for the reviewer (4, 5, 7), and promotes a table only after its inputs. The log adds one column to the standard's fields, `preparer`, so the tool can tell that the reviewer is a different person. State on 2026-11-07: 6 datasets **ready for sign-off** (the preparer has proposed a disposition for every warning), 4 tables **held** until their inputs are promoted, 0 promoted.

## 6. The 14 findings of the integration review (W3-AYE-05)

| # | Finding | Resolution |
|---|---|---|
| 1 | StatCan descriptions only repeat the dataset name | **Fixed:** catalogue descriptions |
| 2 | `revision_policy` and `priority` empty for StatCan | **Fixed:** `revision_policy` (`unknown`, `scheduled`) and `priority` (`high`, the priority of W1-SOL-02) |
| 3 | No owner, reviewer or sign-off; records are rewritten | **Fixed:** promotion log keyed to the raw checksum, and the readiness tool |
| 4 | Merge `rai_checks.py` into `scripts/validation` | **Done**, from the standard and her tests (her module was not accessible); tests 44 to 80 |
| 5 | No INFO in the hub | **Fixed** |
| 6 | Labour Force 27.8% blank reads as pass | **Fixed:** the 5% `missing_share` check warns. Proposed disposition: Accept, because all 175,958 blanks carry `..` and fall before each series' first published value |
| 7 | Analysis-ready tables drop status and series qualifiers | **Fixed:** tables keep `status`, `status_category`, `series_id` and the full series label (e.g. "... Estimate \| Seasonally adjusted") |
| 8 | No record for analysis-ready tables; averaging rule undocumented | **Fixed:** a record per table in `metadata/analysis_ready/`; derived rows marked and recomputed by the validator |
| 9 | Confirmed High from same-day retrievals | **Fixed:** different days required. The BoC datasets are now Potential High: their data changed between 2026-10-24 and 2026-11-07 because new days were published (every earlier value was reproduced unchanged). Two retrievals on days with no release (a weekend or holiday) would confirm them |
| 10 | Processed files lack `series_id` and geography code | **Fixed** |
| 11 | Household survey is biennial but frequency says annual | Recorded in `update_frequency` and `known_limitations`; the ID stays (IDs are permanent). The period-change threshold does not yet know the spacing: open |
| 12 | Fixed thresholds flag real events | Open: defaults kept. CORRA and household period-change warnings have proposed dispositions explaining the real events |
| 13 | Event tables need their own layout | Open: event-table checks exist in `rai_checks.py`; the layout itself is for the provincial integration (W5-AAH-01) |
| 14 | Attribution in the Week 3 integration note | **Corrected** in `documentation/integration_note_week3.md` |

## 7. Still open

- **Ayesha's Week 5 files** (W5-AYE-01 to 04) and her original `rai_checks.py` were not available. When shared, compare:
  - her final field names and required/optional list with section 2;
  - her `status` / value-status treatment with `status_category`;
  - her QA results and exception register with `documentation/qa_log.csv` and the promotion log;
  - her module with the hub's (her 12 tests already pass on the hub's version).
- **Reviewer sign-off:** the promotion log has proposed dispositions; a reviewer other than the preparer confirms or changes them and signs.
- **Threshold calibration** per dataset (`period_change`, `missing_share`), including biennial spacing for household spending.
- **QA-XF1** (totals equal components) needs each table's category hierarchy.
- **Event-table layout** and a shared province lookup, with the provincial work.
- **BoC Confirmed High:** two retrievals on days with no release.
