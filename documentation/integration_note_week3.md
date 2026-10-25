# Week 3 integration note: hub ingestion vs. team standards (W3-SOL-03)

**Date:** 2026-10-24 · **Author:** Solomon Ador-Dionisio · **For:** Maurya Shah, Ayesha Malhi, Aahan Patel, Dr. James Hurst (RAI)

This note checks what the hub's acquisition scripts actually produce against the team's Week 3 standards documents, records the conflicts found, and lists the repository changes made as a result.

## 1. Inputs

| Document | Author | Used for | Status |
|---|---|---|---|
| W3-MAU-01 *Bank of Canada Ingestion Specification* | Maurya Shah | The four Bank of Canada datasets: IDs, endpoints, frequency, units, history, revision policy and refresh schedule | Used in full (sections 4 and 5) |
| W3-MAU-03 *Updated Source Catalogue & Common Metadata Mapping* | Maurya Shah | The 16-field metadata crosswalk, the 26-source catalogue and its proposed dataset IDs, four proposed execution fields, open questions | Used in full (sections 2, 3 and 6) |
| Metadata / data-dictionary standard and 30-check QA framework (W1-AYE-01, W1-AYE-02, W2-AYE-01) | Ayesha Malhi | Field definitions and QA checks | **Skipped: no files from Ayesha were available.** See section 7 |

## 2. Metadata field crosswalk

Maurya's Table 1 maps 16 inventory fields to hub fields. Each row was checked against the metadata records the scripts now write (36 fields, the same for both publishers). Where the hub has changed since W3-MAU-03 was written, the last column says so.

| Inventory field | Hub field | Status after Week 3 | Note |
|---|---|---|---|
| Dataset ID | `dataset_id` | Match | Formula `source_tablenumber_subject_frequency`; permanent once assigned |
| Source organization | `source_organization` | Match | A separate field since Week 2 (W3-MAU-03 still describes it as embedded in the ID) |
| Dataset / table name | `dataset_name` | Match | |
| Province / geography | `geography` | Match | Holds coverage in the source and in the processed file, as text; no province codes yet (see section 6, Ayesha question 1) |
| Risk category | `subject_category` | Match | Lowercase identifiers, e.g. `interest_rates` |
| Description / relevance | `description` | Partial | `description` holds what the data measures, from the publisher. *Relevance* (why it matters for risk) has no field; it is written in the notebooks' findings |
| Source URL | `source_url` | Match | |
| Dataset / resource ID | `source_table_or_series_id` | Match | Original form kept, e.g. `AVG.INTWO`, `11-10-0222-01` |
| Frequency | `frequency` | Match | Hub vocabulary: daily, weekly, biweekly, monthly, quarterly, semiannual, annual, irregular |
| Historical coverage | `observation_start`, `observation_end` | Match | ISO dates, first day of the period |
| Access format | `access_format` | **Added in Week 3** | e.g. "JSON from a REST API (Valet)" |
| Update timing | `update_frequency`, `refresh_schedule`, `source_last_updated` | **Filled in Week 3** | `update_frequency` was always empty before; it now comes from the catalogue. `refresh_schedule` is new |
| Access restrictions | `access`, `licence` | Match | W3-MAU-03 lists no direct match, but both fields have existed since Week 2. The Bank of Canada licence now records its conditions (attribution; exchange rates for statistical use only) |
| Reproducibility rating | `reproducibility_rating` | **Added in Week 3** | Computed, not typed: `Confirmed High` once two retrievals produced the same checksum, otherwise `Potential High`, with the evidence |
| Priority | `priority` | **Added in Week 3** | From the catalogue. Set to `high` for the four Bank of Canada datasets (W3-MAU-01 is a high-priority task); empty for the StatCan tables until the team assigns one |
| Notes / limitations | `known_limitations`, `quality_notes` | **Split in Week 3** | `known_limitations` holds caveats written by people (from the catalogue); `quality_notes` stays the automatic validation summary |

Maurya's four proposed execution fields:

| Proposed field | Decision | Reason |
|---|---|---|
| `primary_key_spec` | Adopted as `primary_key` | `["date", "geography", "series"]`, the key the `duplicates` check already enforces |
| `refresh_cron_schedule` | Adopted as `refresh_schedule` | Written in words with the time zone, e.g. "11:30 ET on business days (CRON_TZ=America/Toronto)", so it reads well outside cron. The cron lines are in `documentation/boc_acquisition.md` |
| `target_8col_schema` | Not adopted | Every processed file in the hub has the same 8 columns, so the field would be identical in every record |
| `verification_status` | Not adopted | Covered by `validation` (the latest checks), `retrieval_history` (every retrieval) and `reproducibility_rating` |

All new fields are written by the scripts, from the catalogue or computed. Nothing is typed into a metadata record by hand, because the next run rewrites it.

## 3. Conflicts found, and the recommended rule

| # | Conflict | Recommended rule |
|---|---|---|
| 1 | W3-MAU-03 lists the Labour Force Survey table as `statcan_14100287_lfs_monthly`; the hub's dataset is `statcan_14100287_labour_force_characteristics_monthly` | Hub IDs are permanent once a metadata record exists. The catalogue should use the hub ID |
| 2 | Several proposed IDs have no publisher-ID part: `boc_bond_yields_daily`, `boc_slos_lending_conditions_quarterly`, `fin_fiscal_monitor_monthly`, `paycan_lynx_volume_value_monthly`, `cmhc_rms_rental_market_annual` | Keep the publisher's ID in every dataset ID (this answers Maurya's question 1). The yields became `boc_bdcdnyld_bond_yields_daily`; SLOS would be `boc_slosbuslend_...`. Where the publisher's ID changes every year (the Fiscal Monitor's open.canada.ca dataset ID), use a stable product code and record the yearly ID in `source_table_or_series_id` |
| 3 | `cmhc_34100143_housing_starts_monthly` is fetched from a Statistics Canada table | The source code names who publishes the file the hub downloads: `statcan_34100143_...`. CMHC is credited in `description` |
| 4 | W3-MAU-03's frequency list is daily, weekly, monthly, quarterly, annual; source #23 is "Unknown – to verify" | Use the hub vocabulary, which adds biweekly, semiannual and irregular. A source whose frequency is unknown is not acquired until it is known |
| 5 | The Bank of Canada declares "Business daily" | Stored as `daily`; `frequency_basis` keeps the Bank's wording: "declared by Bank of Canada (Business daily)" |
| 6 | W3-MAU-03 attributes the 16 inventory fields and the "two matching hashes" reproducibility rule to "Ayesha's Governance Framework (W1-AAH-01/02)". In the project tracker, W1-AAH-01/02 are Aahan Patel's tasks; Ayesha's are W1-AYE-01/02 | Correct the attribution in W3-MAU-03. The hub cites the rule as W1-AAH-02 |
| 7 | W3-MAU-01 says CORRA revisions are "marked 'R'" | The Valet responses checked on 2026-10-24 carry only a value (`v`), with no flag. The script copies any flag that appears into the `status` column, and the `revisions` check reports changed values either way |
| 8 | W3-MAU-03 counts 19 hub metadata fields across 17 rows | Out of date: records now have 36 fields (Week 2 provenance, quality and history; Week 3 governance fields). The README's *Common metadata* section is the reference |

Observation counts in W3-MAU-01 match the hub's live run on 2026-10-24, allowing for the two business days in between: policy rate 4,552 vs 4,554, CORRA 7,292 vs 7,294, yields 6,451 vs 6,453 dates (25,809 values; a few dates have fewer than four tenors, as the spec notes for 2006-09-04), USD/CAD 2,445 vs 2,447.

## 4. Maurya's Bank of Canada specification vs. what was built

| W3-MAU-01 guideline | In the hub |
|---|---|
| Query with `start_date`, never `recent=n` | Adopted, in a stronger form: every run fetches the full history (no `recent`, no `start_date`). At about 4 MB in total this is cheap, and it keeps the hub's rule that the raw file is the complete published data, which is what makes checksums and revision checks meaningful |
| Emit a record only where `v` is present (asymmetric holidays) | Adopted. Holidays create no rows; an observation with an empty `v` is left out and counted by the `empty_values` check |
| Composite primary key `(series_id, observation_date)` | Adopted as `primary_key` = `date, geography, series`, enforced by the `duplicates` check |
| Never overwrite a value with null (`COALESCE` upsert) | Adopted in file form: values are never blanked because absent observations produce no row; every earlier version stays in its raw file; the `revisions` check lists any value that changed or disappeared |
| PostgreSQL table and upsert | Deferred. The hub stores files. The DDL in W3-MAU-01 is the migration path when a database is introduced: the processed files already have the key it requires |
| Cron at 11:30 ET (and 17:15 ET for USD/CAD) with `CRON_TZ=America/Toronto` | Documented, not deployed: recorded in each dataset's `refresh_schedule`, with cron and Windows Task Scheduler instructions in `documentation/boc_acquisition.md`. `check_refresh_readiness.py` lists datasets due for a refresh |
| `urllib` fetcher with retries | The hub's shared `http()` (requests, three attempts with back-off) is used instead, with a clear message when the Bank does not recognize a series |
| Units: "CAD per 1 USD" | The hub stores the Bank's own unit text: "Canadian dollars per US dollar" |

## 5. Repository changes made

- `sources/boc_series.csv` (new) and `sources/statcan_tables.csv`: columns `update_frequency`, `refresh_schedule`, `revision_policy`, `priority`, `known_limitations`. Bank of Canada values come from W3-MAU-01. For the StatCan tables, `revision_policy` and `priority` are left empty because neither is documented in the inputs above.
- Every metadata record: new fields `access_format`, `refresh_schedule`, `revision_policy`, `primary_key`, `reproducibility_rating`, `known_limitations`, `priority`; `update_frequency` now filled.
- README *Common metadata* updated to describe every field.

As of 2026-10-24 all six datasets rate `Confirmed High`: the two StatCan tables from three identical retrievals (2026-10-10, 10-18 and 10-24), the four Bank of Canada datasets from two identical retrievals on the same day (2026-10-24). A repeat on a later day would strengthen the Bank of Canada evidence.

## 6. Answers to the open questions in W3-MAU-03

**For Solomon**

1. *Exact publisher number in every ID?* Yes. The publisher's ID is what lets anyone trace a dataset back to its source, and it keeps IDs unique when two datasets share a subject. Punctuation is removed (`AVG.INTWO` gives `avgintwo`); a dataset with several series uses an `id_part` set in the catalogue.
2. *A shared helper for open.canada.ca?* Agreed, as a next step. The Week 3 adapter structure is designed for it: an `opencanada` adapter would resolve the current resource URL for a stable dataset name, then hand the bytes to the shared core, which already handles versioning, checksums and metadata. Not built this week.
3. *Fiscal vs. calendar quarters?* Agreed with Maurya's suggestion: processed files keep the publisher's own period (OSFI fiscal quarters stay fiscal, labelled by their first month), the metadata records which convention applies, and a mapping table is applied when building analysis-ready tables that mix the two.

**For Ayesha** (left for Ayesha to answer; the hub's current behaviour, for reference)

1. *Province name to code lookup?* The hub keeps the publisher's names and, for StatCan, matches DGUIDs. A shared lookup table would fit in `sources/` and could be used by any adapter.
2. *Restrictive licences?* `licence` already holds the name, URL and, for the Bank of Canada, the conditions; `access` describes restrictions. A restrictive licence (e.g. Ontario.ca Terms of Use) would be recorded the same way.
3. *Repeat runs to confirm reproducibility?* `reproducibility_rating` is now computed on every run, so a second scheduled run of any source confirms it automatically.

## 7. Not done: alignment with Ayesha's standard

No files from Ayesha were available this week (the metadata/data-dictionary standard, its Week 2 harmonized version, or the 30-check QA framework), so the hub has **not** been checked against them. When they are shared, the comparison should cover:

- her field dictionary against the 36 fields in the README's *Common metadata*: names, definitions, required or optional;
- her 30 QA checks against the checks the hub runs (`scripts/validation/hub_checks.py`, plus the StatCan and Bank of Canada checks): implemented, partly, not yet, or not applicable;
- her PASS / WARNING / FAIL handling (W3-AYE-04) against the hub's pass / warn / fail.

Recommendation for W3-AYE-02 (reusable automated QA checks): add the new checks to `hub_checks.py` rather than starting a separate script. Every publisher's acquisition then runs them automatically, and their results are stored in each metadata record under `validation`.

## 8. Still open

- `revision_policy` and `priority` for the StatCan tables.
- Ayesha's standard (section 7).
- A server or scheduled task to run the refresh schedule; a database if the team adopts W3-MAU-01's PostgreSQL design.
