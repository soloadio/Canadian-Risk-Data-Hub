# Changelog

Changes to the hub's scripts, conventions and documentation, newest first.

## v0.3: Week 3 (2026-10-24)

A second publisher, the Bank of Canada (W3-SOL-01), on a shared core split out of the StatCan script (W3-SOL-02), alignment with the team's source catalogue and metadata work (W3-SOL-03), and refresh-readiness checks with a larger offline test suite (W3-SOL-04). Verified with 44 automated tests (`python -m pytest tests`) and live runs against both publishers on 2026-10-24.

### What changed

| Area | Week 2 | Risk | Week 3 change |
|---|---|---|---|
| Publishers | Statistics Canada only; storage, IDs, history and metadata code lived inside the StatCan script | Each new publisher would copy 700 lines and drift from the others | `scripts/common/hub_core.py` holds HTTP retries, stable IDs, raw versioning, history and atomic metadata writing for every publisher. The StatCan script keeps only StatCan logic and writes metadata identical to Week 2 (checked by running the Week 2 and Week 3 code on the same input) |
| Bank of Canada | Not in the hub | The hub was only shown to work for one publisher | `scripts/acquisition/boc/download_boc_series.py` (Valet API): policy rate, CORRA, benchmark yields (2, 5, 10-year, long) and USD/CAD, following Maurya's W3-MAU-01 specification. Full history on every run, raw JSON kept as received, holidays create no empty rows |
| Validation | StatCan checks in one file; date gaps only for monthly and longer data | New publishers would need their own copy; daily data was never checked for gaps | Shared checks in `scripts/validation/hub_checks.py`. New for every publisher: `date_gaps` for daily/weekly data (no series more than a week without an observation), `freshness` (latest observation recent enough) and `revisions` (values changed or removed since the previous retrieval). BoC checks: `series_present`, `future_dates`, `units`, `terms`, `empty_values` |
| Re-checking | `validate_statcan_dataset.py` for StatCan files | No re-check for other publishers | `scripts/validation/validate_dataset.py --all` re-checks every publisher's processed files. The StatCan command still works |
| Refresh readiness | Not checked | An edited raw file, a missing processed file or a catalogue entry never downloaded would only show up during a refresh | `scripts/validation/check_refresh_readiness.py`: raw files still match their checksums, processed files exist, no failed validation, catalogues and records match, datasets due for a refresh listed. Offline; exit code 1 on a problem |
| Metadata | `update_frequency` always empty; no field for human caveats, priority or reproducibility | Records could not carry the team's inventory fields (W3-MAU-03 crosswalk) | Catalogue columns `update_frequency`, `refresh_schedule`, `revision_policy`, `priority`, `known_limitations` copied into every record; computed `access_format`, `primary_key`, `reproducibility_rating` |
| Tests | 15 StatCan tests | Shared code had no tests of its own | 44 tests, all offline: StatCan (16), Bank of Canada with a fake Valet service (10), shared core and checks (13), refresh readiness (5). GitHub Actions runs them on every push |
| Analysis | Labour force and household notebooks | No cross-publisher example | `notebooks/03_bank_of_canada_rates.ipynb`: policy rate vs CORRA, yield-curve slope, USD/CAD, and `analysis_ready/rates_and_unemployment_monthly.csv`, the first table combining Bank of Canada and Statistics Canada data |

### New and changed files

- `scripts/common/hub_core.py` (new): the shared core.
- `scripts/acquisition/boc/download_boc_series.py` (new) and `sources/boc_series.csv` (new): the Bank of Canada adapter and its catalogue.
- `scripts/validation/hub_checks.py` (new), `validate_dataset.py` (new), `check_refresh_readiness.py` (new); `validate_statcan_dataset.py` now holds only StatCan checks and re-exports the shared ones.
- `scripts/acquisition/statcan/download_statcan_table.py`: uses the shared core; reads the new catalogue columns, also for one-off `--table` runs.
- `sources/statcan_tables.csv`: new catalogue columns filled where documented.
- `tests/test_boc_acquisition.py`, `tests/test_hub_core.py`, `tests/test_refresh_readiness.py` (new); `.github/workflows/tests.yml` (new).
- `notebooks/03_bank_of_canada_rates.ipynb` (new), two analysis-ready tables and three charts in `outputs/`.
- `documentation/boc_acquisition.md` (new), `documentation/integration_note_week3.md` (new), `README.md`, `documentation/statcan_acquisition.md`.
- `.gitignore`: raw JSON files are excluded like raw CSVs.
- Four new Bank of Canada metadata records; both StatCan records refreshed.

### Results of the 2026-10-24 live runs

| Dataset | Raw / processed | Validation | Reproducibility |
|---|---|---|---|
| `boc_v39079_policy_rate_daily` | 4,554 dates / 4,554 values, 2009-04-21 to 2026-10-22 | Passed | Confirmed High (2 identical retrievals, same day) |
| `boc_avgintwo_corra_daily` | 7,294 / 7,294, 1997-08-12 to 2026-10-22 | Passed | Confirmed High (2, same day) |
| `boc_bdcdnyld_bond_yields_daily` | 6,453 dates / 25,809 values (4 series), 2001-01-02 to 2026-10-22 | Passed | Confirmed High (2, same day) |
| `boc_fxusdcad_exchange_rate_daily` | 2,447 / 2,447, 2017-01-03 to 2026-10-23 | Passed | Confirmed High (2, same day) |
| `statcan_11100222_household_spending_annual` | 44,265 / 3,405 rows; same checksum as Weeks 1 and 2 | Passed with warnings: the same date gaps as Week 2, plus `freshness` (latest year 2023) | Confirmed High (3 identical retrievals) |
| `statcan_14100287_labour_force_characteristics_monthly` | 5,475,519 / 633,969 rows; same checksum as Weeks 1 and 2 | Passed | Confirmed High (3) |

Both StatCan dataset IDs are unchanged. The Bank of Canada was run twice on 2026-10-24; the second run found the data unchanged and reused the stored raw files. `validate_dataset.py --all` and `check_refresh_readiness.py` report no failures and no problems for the six datasets.

### Not done yet

- **Alignment with Ayesha's metadata standard and QA framework was skipped:** no files from Ayesha were available. `documentation/integration_note_week3.md` (section 7) lists what to compare when they are shared.
- `revision_policy` and `priority` are empty for the two StatCan tables; neither is documented in the inputs used this week.
- Refreshes are not scheduled: `refresh_schedule` records when to run, and `documentation/boc_acquisition.md` shows how, but there is no server yet. The PostgreSQL design in W3-MAU-01 is noted as the path if a database is adopted.
- The Bank of Canada `reproducibility_rating` rests on two retrievals on the same day; a repeat on a later day would be stronger evidence.
- Processed files still do not carry a stable series identifier (StatCan vector, Valet series name) beside the label.

## v0.2: Week 2 (2026-10-18)

Hardening and validation of the Statistics Canada acquisition prototype (W2-SOL-01), and documentation and provenance updates (W2-SOL-02). Verified with a live run against Statistics Canada on 2026-10-18 for both catalogued tables, and with 15 automated tests (`python -m pytest tests`).

### Problems found in the Week 1 prototype, and what changed

| Area | Week 1 behaviour | Risk | Week 2 change |
|---|---|---|---|
| Stable dataset IDs | ID rebuilt on every run from the table title and the frequency detected from the dates | A renamed table, or a survey moving to every second year, silently creates a new dataset with a new ID. The household spending table would be detected as `irregular` after four more biennial releases | An existing metadata record pins the ID. New IDs use the frequency StatCan declares (`frequencyCode`), falling back to detection. Subjects are now set in the catalogue |
| Raw-file versioning | A second run on the same day overwrote that day's raw file | A revised table could replace the earlier version, breaking the "raw is never edited" rule | Raw files are never overwritten (`_2`, `_3` counters). Unchanged data is not copied again. Each retrieval is logged in `retrieval_history` |
| Geography | A geography not found in the table silently fell back to all geographies | A typo in the catalogue produces a file with the wrong coverage and a misleading name | Unknown geography stops the table with the list of valid names. Matching ignores capitals, accepts DGUIDs and several places separated by `;`. Names shared by several DGUIDs are flagged |
| Missing values | One count of missing values | "Not available", "suppressed" and "too unreliable" could not be told apart in the summary | Missing values counted by StatCan status. Missing values with no status, and published values carrying a "missing" status, are flagged |
| Non-numeric values | Silently turned into missing values | A layout change could empty the value column unnoticed | Any non-numeric value fails the table |
| Duplicates | Not checked | Duplicate rows would double-count in analysis | Exact duplicates removed with a warning. Conflicting duplicates (same date, geography and series, different value) fail the table |
| Date gaps | Not checked | Periods missing from a series go unnoticed | Periods missing from the whole table and from individual series are reported for monthly, quarterly, semiannual and annual data |
| Schema changes | Only the four required columns were checked | Renamed or added dimensions change the series labels that notebooks rely on | Columns, dimensions and units are recorded under `schema`. A change raises `schema_version` and is reported |
| Completeness | Not checked | A truncated download would pass unnoticed | Rows received are compared with the data-point count StatCan reports |
| Failure handling | Files written before any checks | A bad download could replace good processed data | Checks run first. On failure the processed file and metadata keep their previous version. Files are written atomically |

### New and changed files

- `scripts/validation/validate_statcan_dataset.py` (new): all quality checks. Also runs on its own to re-check processed files on disk.
- `scripts/acquisition/statcan/download_statcan_table.py`: stable IDs, raw versioning, validation, new provenance fields.
- `tests/test_statcan_acquisition.py` (new): offline tests with a fake StatCan service.
- `sources/statcan_tables.csv`: `subject` filled in for both tables, matching their existing IDs.
- `README.md`: metadata specification reorganized and extended, new *Validation* and *AI use* sections, versioning and ID rules.
- `documentation/statcan_acquisition.md`: rerun behaviour, validation, known warnings, troubleshooting.
- `requirements.txt`: `pytest` added.
- Both metadata records regenerated by the live run.

### New metadata fields

`source_organization`, `licence`, `access`, `retrieval_method`, `frequency_basis`, `schema_version`, `schema`, `validation`, `retrieval_history` and `ai_use_note`. The README's *Common metadata* section describes each one. Week 1 said publisher and retrieval method would not be separate fields; that was reversed so each record is self-describing for readers and AI applications that see only the record.

### Results of the 2026-10-18 live run

| Table | Rows (raw / processed) | Data changed since Week 1 | Validation |
|---|---|---|---|
| 11-10-0222-01 Household spending | 44,265 / 3,405 | No (same checksum) | Passed with warnings: 2018, 2020 and 2022 absent from the table (the survey became biennial); one series skips a year |
| 14-10-0287-01 Labour force characteristics | 5,475,519 / 633,969 | No (same checksum) | Passed: no gaps in 609 months, all 175,958 missing values carry the `..` status |

Both dataset IDs are unchanged, so the notebooks and analysis-ready tables need no changes.

### Not done yet

- `update_frequency` (how often StatCan publishes) is still empty. StatCan's API gives the data frequency, not the release schedule.
- Processed files do not carry StatCan's vector ID per series, which would give each series a stable identifier independent of its label.
- Validation for daily, weekly and biweekly data does not check for date gaps.

## v0.1: Week 1

First prototype: repository structure, naming conventions, metadata specification, the Statistics Canada acquisition script, and analysis notebooks for labour force and household spending.
