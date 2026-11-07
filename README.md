# Canadian Risk Data Hub

A structured, repeatable process for identifying, acquiring, cleaning, maintaining and analyzing Canadian economic, financial and business-risk data.

Part of the Risk Analytics Institute project *Building an AI-Ready Canadian Risk Data Hub* (Oct 5 – Nov 2026).

> **Status:** Draft v0.4 (Week 5). The hub follows the team's metadata and QA standard (RAI_Metadata_QA_Standard_v1.0), keeps each publisher's stable series ID, validates its analysis-ready tables and has a promotion and sign-off process. To check it end to end without downloading anything, run `python demo/run_demo.py` (see [demo/README.md](demo/README.md)). See [documentation/changelog.md](documentation/changelog.md) for what changed.

**Data in the hub**

| Publisher | Datasets | Script | Guide |
|---|---|---|---|
| Statistics Canada | Labour force characteristics (14-10-0287-01), household spending (11-10-0222-01) | `scripts/acquisition/statcan/download_statcan_table.py` | [statcan_acquisition.md](documentation/statcan_acquisition.md) |
| Bank of Canada | Policy rate (V39079), CORRA (AVG.INTWO), benchmark bond yields (2, 5, 10-year and long), USD/CAD (FXUSDCAD) | `scripts/acquisition/boc/download_boc_series.py` | [boc_acquisition.md](documentation/boc_acquisition.md) |

---

## Goals

- One consistent place for every dataset the team acquires.
- Every dataset documented with the same metadata, so it can be understood, trusted and reused by a new student, an RAI analyst, a future maintainer, or an AI application.
- Every acquisition reproducible: anyone can rerun a script and get the same raw data, and refresh it later.

The data lifecycle this repository supports:

```
Source identification -> evaluation -> repeatable acquisition -> raw storage
-> standardization -> documentation -> quality control -> analysis-ready data -> analytical/AI use
```

---

## Repository structure

```
Canadian-Risk-Data-Hub/
├── notebooks/            # Notebooks to study and analyze the datasets
├── sources/              # One catalogue per publisher: what to download, and its update cadence
├── raw/                  # Data exactly as received
│   └── <source>/<dataset_id>/
├── processed/            # Cleaned, standardized data
│   └── <source>/<dataset_id>/
├── analysis_ready/       # Final tables for analysis, dashboards, modelling, AI use
├── metadata/             # One metadata record per dataset
│   └── analysis_ready/   # One record per analysis-ready table
├── scripts/              # Acquisition, transformation and validation code
│   ├── acquisition/<source>/   # One adapter per publisher (statcan/, boc/)
│   ├── common/                 # Shared core: storage, IDs, history, metadata
│   ├── processing/             # Builds the analysis-ready tables
│   └── validation/             # Shared, team-standard and publisher-specific checks; promotion readiness
├── tests/                # Automated tests for the scripts (no network needed)
├── .github/workflows/    # Runs the tests on every push
├── demo/                 # Run the whole hub on small samples, offline, and check the results
├── documentation/        # Guides, changelog, crosswalks, promotion log and QA log
├── outputs/              # Charts, tables and summaries produced from the data
├── requirements.txt      # Python dependencies
└── .gitignore
```

**Rules of thumb**

- `raw/` is never edited by hand or overwritten. If something needs fixing, fix it in code so the change is reproducible.
- Large data files may be excluded from Git (see `.gitignore`). The script and the metadata record are what make them reproducible.

---

## Raw vs. processed vs. analysis-ready data

| Stage | What it is | Allowed changes | Example |
|---|---|---|---|
| **Raw** | Data exactly as received from the provider | None. One file per version of the data, never edited or overwritten | Original StatCan CSV with its original columns and symbols |
| **Processed** | Cleaned, standardized version of one raw dataset | Standardize dates, rename variables, convert numeric formats, harmonize units, remove duplicates, standardize missing values | Long-format table (see *Processed layout*) |
| **Analysis-ready** | Tables organized for a specific analytical use | Selecting rows and series, joining datasets, derived measures (documented and labelled as derived) | Monthly policy rate, 10-year yield and USD/CAD with the unemployment rate |

Each stage is generated from the previous one by a script, never by hand: the acquisition scripts write raw and processed files, and `scripts/processing/build_analysis_ready.py` builds the analysis-ready tables. This keeps the original source data preserved and every transformation reproducible. Notebooks read these tables and make charts; they do not write data.

**Processed layout.** Every processed file has the same 11 columns, for every publisher:

| Column | Contents |
|---|---|
| `date` | Reference period as an ISO date (first day of the period) |
| `geography` | Readable geography name |
| `series` | Readable series label (the table's dimensions joined with ` \| `) |
| `value` | The number, or empty when the publisher gives none |
| `unit` | Unit and scaling for the row |
| `source` | Publisher code (`statcan`, `boc`) |
| `dataset_id` | The dataset the row belongs to |
| `status` | The publisher's status symbol, as published (e.g. `..`, `x`, `F`); empty if none |
| `status_category` | The team standard's category for that symbol (`not_available`, `suppressed`, `unreliable`, `quality_flag`, ...); `unknown` for a symbol the standard does not list |
| `geo_code` | The publisher's official geography code (StatCan DGUID); empty when it has none (Bank of Canada) |
| `series_id` | The publisher's stable series identifier: the StatCan vector (`v2062815`) or the Valet series name (`BD.CDN.10YR.DQ.YLD`). Use it for joins: it does not change when a label is reworded |

**Analysis-ready tables** keep these columns (dataset_id, unit, status and series_id at least), plus readable columns such as `category` or `age_group`, and a `derivation` column when some rows are computed. Each has a record in `metadata/analysis_ready/` with its description, inputs (and the raw checksum each was built from), selection, derivation rules, key, known limitations and validation results.

---

## Naming conventions

**Every dataset gets one ID**, built from four parts joined by underscores:

```
source _ table number _ subject _ frequency
```

Example: `statcan_18100004_cpi_monthly`

- `statcan`: who publishes it (`boc` for Bank of Canada, and so on)
- `18100004`: the publisher's own table or series number, without punctuation, so the data can always be traced back. For the Bank of Canada this is the series name (`V39079` gives `v39079`, `AVG.INTWO` gives `avgintwo`); a dataset holding several series uses the `id_part` set in the catalogue (the benchmark yields use `bdcdnyld`)
- `cpi`: what it measures, in one or two words
- `monthly`: how often the data is observed (`daily`, `weekly`, `biweekly`, `monthly`, `quarterly`, `semiannual`, `annual`, or `irregular` when no regular spacing is found)

**Use that ID in every file that belongs to the dataset:**

| Stage | File name |
|---|---|
| Raw | `statcan_18100004_cpi_monthly_20261009.csv` |
| Raw (table description from StatCan) | `statcan_18100004_cpi_monthly_20261009_cube_metadata.csv` |
| Raw (Bank of Canada, kept in the format received) | `boc_v39079_policy_rate_daily_20261024.json` |
| Processed | `statcan_18100004_cpi_monthly.csv` |
| Metadata | `statcan_18100004_cpi_monthly.json` |

Only raw files carry a date at the end. It is the day the data was downloaded (not the period the data covers). A new raw file is written only when the data differs from the last one stored; if it is unchanged, the existing file is reused and the retrieval is still logged in the metadata. If the data changes twice in one day, the second file gets a counter (`..._20261009_2.csv`), so a raw file is never overwritten. Statistics Canada ships a description of the table (dimensions, notes, footnotes) alongside the data, and it is saved next to the raw file.

**IDs are permanent.** Once a dataset has a metadata record, its ID is reused on every later run, even if the source renames the table or its publication pattern changes. The frequency in a new ID comes from the frequency the publisher declares, and is only detected from the dates when the publisher gives none. Set `subject` in the source catalogue so the ID does not depend on the table title. Renaming a dataset is a deliberate step: rename its metadata record and folders together.

**Analysis-ready tables** can combine several datasets, so they are named after what they contain instead of a dataset ID: subject, then frequency, for example `unemployment_rate_by_age_monthly.csv`. They carry a `dataset_id` column so every row can be traced back to its source.

**General rules:** all lowercase, underscores instead of spaces, and scripts start with a verb (`download_statcan_table.py`). Geography is left out of the ID because many tables cover several, and the stage (raw, processed) is shown by the folder.

---

## Common metadata

Every dataset has one metadata record in `metadata/` (`<dataset_id>.json`). The acquisition script fills in everything it can from the source, so the record is created and refreshed automatically each time the script runs.

**Identification and provenance**

| Field | Description |
|---|---|
| `dataset_id` | Internal unique identifier (see naming conventions). Never changes once assigned |
| `dataset_name` | Human-readable name, taken from the source's table title |
| `description` | What the data measures |
| `source_organization` | Organization that publishes the data, e.g. `Statistics Canada` |
| `source_url` | Page where the data is published |
| `source_table_or_series_id` | Provider's table or series number |
| `licence` | Licence name and URL under which the data may be used (Statistics Canada: Open Licence; Bank of Canada: Terms of Use, with its conditions) |
| `access` | How the data can be accessed: public or restricted, free or paid, account or key needed |
| `access_format` | The format the data arrives in, e.g. CSV in a ZIP archive, or JSON from a REST API |
| `retrieval_method` | How the data is fetched: service, endpoint and file format |
| `retrieval_script` | Path of the script that fetched the data |
| `ai_use_note` | How AI tools were used in producing the dataset and what was validated. States that no data values were generated or changed by AI |

**Content**

| Field | Description |
|---|---|
| `subject_category` | inflation, labour, credit, housing, interest_rates, etc. Taken from the source catalogue (`sources/statcan_tables.csv`, `sources/boc_series.csv`) and lowercased; `uncategorized` if left blank there |
| `geography` | Geographic coverage in the source, and the coverage kept in the processed file (Canada by default; one or more places, or all, if requested) |
| `frequency` | Observation frequency (see naming conventions for the values) |
| `frequency_basis` | Where the frequency came from: declared by the publisher, detected from the dates, or taken from the existing dataset ID |
| `observation_start` / `observation_end` | First and last reference period, as ISO dates (`2010-01-01`) |
| `units` | Units and scaling (e.g. index 2002=100, persons in thousands) |
| `schema_version` | Version of the raw table's layout. Starts at 1 and goes up by one when the source adds, removes or reorders columns or dimensions |
| `schema` | The raw columns, the table's dimension columns and its units, as of the latest retrieval |

**Retrieval and quality**

| Field | Description |
|---|---|
| `last_retrieved` | Timestamp of the latest retrieval, with UTC offset |
| `raw_file_location` | Path to the raw file holding the latest data |
| `processed_file_location` | Path to the processed file |
| `update_frequency` | When the publisher releases new data, in words (e.g. "monthly (Labour Force Survey, released early in the following month at 08:30 ET)"). From the source catalogue |
| `refresh_schedule` | When the hub should re-run the acquisition, with the time zone. From the source catalogue |
| `revision_policy` | Whether the publisher revises published values: `none`, `scheduled`, `ad_hoc` or `unknown` (team standard v1.0). From the source catalogue; the details are in `known_limitations` |
| `source_last_updated` | Provider's own release time for the table, with UTC offset, if available (the Bank of Canada's API does not give one) |
| `checksum` | SHA-256 of the raw file, used to tell whether the data changed since the previous retrieval |
| `primary_key` | The columns that identify one observation in the processed file: `date`, `geography`, `series_id` (the `duplicates` and `series_identity` checks enforce it) |
| `reproducibility_rating` | `Confirmed High` once retrievals on at least two different days produced byte-identical raw data (same checksum), otherwise `Potential High`, with the basis for the rating (team standard v1.0: same-day repeats do not count) |
| `quality_notes` | One-paragraph summary of the validation results, for people reading the record |
| `known_limitations` | Caveats a user must know, written by people (e.g. a survey that became biennial, a methodology change). From the source catalogue |
| `priority` | The team's priority for the dataset. From the source catalogue; empty (null) where not yet assigned |
| `validation` | Overall result (`passed`, `passed_with_warnings`) and every individual check with its status and details (see *Validation*) |
| `retrieval_history` | One entry per retrieval: time, checksum, raw file, source release time, whether the data changed, schema version and validation result |

The acquisition script fills in every field and refreshes the record each time it runs. The team standard (RAI_Metadata_QA_Standard_v1.0, Ayesha Malhi) marks 32 of these keys Required (needed to promote the dataset) and 4 Recommended (`refresh_schedule`, `revision_policy`, `source_last_updated`, `priority`); the `metadata_completeness` check reports any that are empty. `description` should say more than the dataset name; StatCan descriptions come from the catalogue's `description` column. Owner, reviewer and sign-off are not record keys: they are in the promotion log (see *Promotion and sign-off*). Fields marked "From the source catalogue" are written by people in the catalogue CSV and copied as they are; nothing is added to a metadata record by hand, because the next run would overwrite it.

---

## Conventions

**Dates.** ISO 8601 (`YYYY-MM-DD`). Periodic data uses the first day of the period (August 2026 is `2026-08-01`, Q3 2026 is `2026-07-01`); the `frequency` field records the period length. Timestamps include the UTC offset.

**Missing values.** Left empty (null) in processed files, never `0` and never text such as "n/a" or "..". Statistics Canada uses status symbols (for example `..` for not available and `x` for suppressed); the processed table keeps these in a separate `status` column so "missing" and "suppressed" can be told apart, and adds the standard `status_category` beside it. The Bank of Canada leaves a series out of a date when it has no value (market holidays), so no row is created for it: an absent row means "not published that day", not a missing value.

**Variable names.** Lowercase `snake_case`, no spaces, special characters or accents. Standard names for common columns: `date`, `geography`, `series`, `value`, `unit`, `source`, `dataset_id`, `status`.

**Numbers.** Plain numeric values, with no thousands separators or currency symbols. Units and scaling live in the `unit` column and the metadata.

**Encoding.** UTF-8 for all text files (French accents are common in Canadian sources).

---

## Versioning and workflow

**Code and docs** are tracked in Git. Work on short-lived branches, and review before merging to `main`.

**Workflow**

1. **Acquire:** a script in `scripts/acquisition/<source>/` downloads data to `raw/`.
2. **Document:** the script writes or updates the metadata record in `metadata/`.
3. **Process:** a script in `scripts/processing/` writes the standardized table to `processed/`.
4. **Validate:** checks in `scripts/validation/` confirm the output and record the results in `validation` and `quality_notes`. A failed check stops the processed file and metadata from being overwritten.
5. **Prepare:** `scripts/processing/build_analysis_ready.py` builds the analysis-ready tables from processed data and validates them.
6. **Review:** reproducibility, clarity and integration are then checked.

---

## Validation

Every retrieval is checked automatically before the processed file and metadata are written. The checks follow the team's metadata and QA standard (RAI_Metadata_QA_Standard_v1.0, Ayesha Malhi), whose results are **PASS**, **INFO**, **WARNING** and **FAIL** (written `pass`, `info`, `warn`, `fail` in the records). A FAIL stops that dataset and leaves the previous processed file and metadata in place; the raw file is still kept for inspection. A WARNING is recorded and printed but does not stop the run; before the dataset is promoted, a reviewer records a disposition for it (see *Promotion and sign-off*). INFO is a note that never blocks.

**Hub checks** (`scripts/validation/hub_checks.py`, plus each publisher's own):

| Check | What it looks at | Result when something is found |
|---|---|---|
| `schema` | Columns and dimensions compared with the previous retrieval | WARNING; raises `schema_version` |
| `row_count` | Rows received compared with the number of data points the publisher reports (StatCan) | WARNING |
| `geography` | The requested geography exists in the table (case-insensitive, by name or DGUID) (StatCan) | FAIL |
| `series_present`, `units`, `terms`, `empty_values` | Every requested series came back with values (FAIL); one unit per dataset; the terms-of-use link; empty values (Bank of Canada) | FAIL / WARNING |
| `dates` | Every reference period converts to an ISO date | WARNING |
| `numeric_values` | Every non-empty value is a number | FAIL |
| `terminated_series` | Series the publisher no longer updates (StatCan) | INFO |
| `duplicates` | Two rows with the same date, geography and series | FAIL if their values differ; exact copies removed with a WARNING |
| `series_identity` | Each `series_id` has one label and each label one `series_id` | FAIL on a clash; WARNING if IDs are missing |
| `missing_values` | Every missing value has a status explaining it, and no published value carries a "missing" status | WARNING if unexplained; INFO if all explained |
| `date_gaps` | Periods absent from the whole table or from a series (monthly to annual data); stretches of more than a week without an observation (daily data) | WARNING |
| `revisions` | The new processed file compared with the previous one, matched on `series_id`: values changed, observations removed, series relabelled | INFO (the earlier version stays in its raw file) |
| `freshness` | Whether the latest observation is as recent as the frequency and the usual publication lag allow (e.g. 75 days for monthly data) | WARNING |

**Team-standard add-on checks** (`scripts/validation/rai_checks.py`, the standard's 30-check list; Ayesha's tests in `tests/test_rai_checks.py`):

| Check | ID | What it looks at | Result |
|---|---|---|---|
| `key_fields` | QA-CMP1 | No empty date, geography, series or dataset_id | FAIL |
| `missing_share` | QA-CMP2 | Share of empty values, against a 5% limit | INFO up to 5%, WARNING above |
| `value_range` | QA-VAL1 | Percentages between 0 and 100 | WARNING |
| `percent_convention` | QA-VAL2 | Percentages stored as points (5.2), not decimals (0.052); standard errors excluded | WARNING |
| `status_symbols` | QA-VAL3 | Only status symbols the standard knows | INFO if present and known, WARNING if unknown |
| `unit_consistency` | QA-XF2 | One unit per series | FAIL |
| `future_dates` | QA-TS2 | No observation dated after today | FAIL |
| `period_change` | QA-TS4 | Period-over-period changes far outside each series' usual range, listed for review (real events are flagged too) | INFO up to 1% of changes, WARNING above |
| `metadata_completeness` | QA-PRV1 | Every Required key filled; description more than the name; `revision_policy` in the standard's list | WARNING (INFO if only Recommended keys are empty) |
| `metadata_consistency` | QA-PRV2 | The table agrees with its record (dataset_id, first and last dates) | WARNING |

The thresholds are the standard's starting defaults, to be calibrated per dataset. Event-table checks (`text_sentinels`, `constant_columns`, `category_variants`, `multi_valued`, `zero_as_missing`) are in the same module for future non-time-series sources.

**Analysis-ready checks** (`scripts/validation/validate_analysis_ready.py`), run every time a table is built: `table_layout` (dataset_id, unit, status and series_id kept), `key_fields`, `table_key` (the declared key is unique), `duplicate_rows`, `nulls_explained` (every empty value carries the publisher's status), `provenance` (every row matches a published observation in its processed file, with the same value and status, and every derived value matches its rule recomputed from the source; this is what catches rows invented by reshaping), `derivations_documented`, `inputs_current` (built from the data now on record), `unit_consistency` and `future_dates`.

To re-run the checks on files already on disk, without downloading:

```bash
python scripts/validation/validate_dataset.py --all          # processed files, every publisher
python scripts/validation/validate_analysis_ready.py --all   # analysis-ready tables
```

**Reprocessing.** After a change to the processing code, rebuild the processed files and records from the stored raw files instead of downloading again (the stored file's checksum is verified first, and no retrieval is logged):

```bash
python scripts/acquisition/statcan/download_statcan_table.py --from-raw
python scripts/acquisition/boc/download_boc_series.py --from-raw
```

**Refresh readiness.** Before a scheduled refresh, check that the hub is in a good state, without downloading anything:

```bash
python scripts/validation/check_refresh_readiness.py
```

It confirms that every raw file still matches its recorded checksum, every processed file exists, no last validation failed, each catalogue entry has been acquired and each record is still in a catalogue, and it lists datasets due for a refresh. It exits with code 1 if anything would break the next refresh.

**Tests and demo.** Run the automated tests with `python -m pytest tests` (80 tests). They replace the publishers with fake services, so they need no network and no large download. `python demo/run_demo.py` runs the whole hub on small samples and compares the results with `demo/expected/` ([demo/README.md](demo/README.md)). GitHub Actions runs both on every push.

---

## Promotion and sign-off

A dataset or analysis-ready table may be used as analysis-ready once it is **promoted**, following the team's workflow (RAI_Promotion_and_Signoff_Workflow, Ayesha Malhi). The criteria, in short:
- its latest validation did not fail;
- every Required metadata key is filled;
- every WARNING has a disposition (Accept with a reason, Fix, or Waive with an approver);
- the manual checks are done and the licence is reviewed;
- reproducibility is Confirmed High (or Potential High with a reviewer note);
- a reviewer other than the preparer signs it off.

```bash
python scripts/validation/check_promotion_readiness.py                 # what each item still needs
python scripts/validation/check_promotion_readiness.py --write-qa-log  # also rewrite documentation/qa_log.csv
```

Sign-off is recorded in `documentation/promotion_log.csv`, one row per review: `dataset_id`, `raw_checksum_reviewed`, `validation_status`, `warnings_total`, `dispositions` (`<check>: <Accept|Fix|Waive> - <reason>`, separated by ` | `), `preparer`, `reviewer`, `review_date`, `decision` (`promoted`, `held` or `waived`), `approver`, `notes`. It is kept out of the metadata records because the scripts rewrite those on every run. A sign-off stands while the reviewed checksum is unchanged; when the data changes, the item needs a new review. A table is promoted only after its inputs. `documentation/qa_log.csv` lists every check result in the standard's QA log layout.

---

## Adding a publisher

Each publisher has a small adapter script in `scripts/acquisition/<source>/`. Everything that should work the same way for every publisher is in `scripts/common/hub_core.py` and `scripts/validation/hub_checks.py`, so an adapter only does what is specific to its publisher:

| The adapter provides | The shared core provides |
|---|---|
| A source catalogue (`sources/<source>_….csv`) and code to read it | Stable dataset IDs (`resolve_dataset_id`): an existing record pins the ID |
| Fetching the data (endpoint, format, publisher error messages) | HTTP with retries and timeouts (`http`) |
| Turning the raw file into the standard processed table | Raw storage: saved as received, a new dated version only when the data changed, never overwritten (`open_dataset`) |
| Publisher-specific checks (status codes, geography, series present, …) | The shared checks, refusing to overwrite outputs when a check fails, the processed file, the metadata record and its retrieval history, all written atomically (`finish_dataset`) |
| Provenance constants: organization, licence, access, retrieval method | Computed fields: checksum, `reproducibility_rating`, `primary_key`, catalogue fields copied into the record |
| `series_id` (the publisher's stable series identifier), `geo_code` and `status` in the processed table | `status_category`, the team-standard checks, reprocessing from the stored raw file (`stored_raw`) |

The Bank of Canada adapter (`download_boc_series.py`, about 400 lines including documentation) is the model to copy. Add offline tests with a fake service, as in `tests/test_boc_acquisition.py`, and a guide in `documentation/`.

---

## AI use

AI coding assistants were used to help write the scripts and documentation in this repository. All code is reviewed by the project team before merging. Data values are never generated, edited or imputed by AI: raw files are kept exactly as published, and every later stage is produced by deterministic code in this repository. Each metadata record carries an `ai_use_note` saying this, alongside the validation results for that retrieval.

---

## Getting started

```bash
git clone https://github.com/soloadio/Canadian-Risk-Data-Hub
cd Canadian-Risk-Data-Hub
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

To check that everything works without downloading anything, run the demo: `python demo/run_demo.py` ([demo/README.md](demo/README.md)).

Acquisition instructions for each source are in `documentation/`: [statcan_acquisition.md](documentation/statcan_acquisition.md) and [boc_acquisition.md](documentation/boc_acquisition.md). How the hub lines up with the team's work is in [metadata_qa_crosswalk_week5.md](documentation/metadata_qa_crosswalk_week5.md) (Ayesha's metadata and QA standard) and [integration_note_week3.md](documentation/integration_note_week3.md) (Maurya's source catalogue and Bank of Canada specification).

---

## Contributors

Risk Analytics Institute project team, Fall 2026.
