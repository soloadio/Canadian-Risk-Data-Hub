# Canadian Risk Data Hub

A structured, repeatable process for identifying, acquiring, cleaning, maintaining and analyzing Canadian economic, financial and business-risk data.

Part of the Risk Analytics Institute project *Building an AI-Ready Canadian Risk Data Hub* (Oct 5 – Nov 2026).

> **Status:** Draft v0.3 (Week 3). The hub now takes data from two publishers, Statistics Canada and the Bank of Canada, through one shared core for storage, metadata and validation. See [documentation/changelog.md](documentation/changelog.md) for what changed.

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
├── scripts/              # Acquisition, transformation and validation code
│   ├── acquisition/<source>/   # One adapter per publisher (statcan/, boc/)
│   ├── common/                 # Shared core: storage, IDs, history, metadata
│   ├── processing/
│   └── validation/             # Shared and publisher-specific checks
├── tests/                # Automated tests for the scripts (no network needed)
├── .github/workflows/    # Runs the tests on every push
├── documentation/        # Methodology, update procedures, known limitations, changelog
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
| **Processed** | Cleaned, standardized version of one raw dataset | Standardize dates, rename variables, convert numeric formats, harmonize units, remove duplicates, standardize missing values | Long-format table: `date, geography, series, value, unit, source, dataset_id` |
| **Analysis-ready** | Tables organized for a specific analytical use | Filtering, reshaping, joining datasets, derived measures | Monthly CPI and unemployment rate merged by province |

Each stage is generated from the previous one by code (a script, or a notebook for analysis-ready tables), never by hand. This keeps the original source data preserved and every transformation reproducible.

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
| `revision_policy` | Whether and how the publisher revises published values. From the source catalogue; empty (null) where not yet documented |
| `source_last_updated` | Provider's own release time for the table, with UTC offset, if available (the Bank of Canada's API does not give one) |
| `checksum` | SHA-256 of the raw file, used to tell whether the data changed since the previous retrieval |
| `primary_key` | The columns that identify one observation in the processed file: `date`, `geography`, `series` (the `duplicates` check enforces it) |
| `reproducibility_rating` | `Confirmed High` once two retrievals produced byte-identical raw data (same checksum), otherwise `Potential High`, with the basis for the rating |
| `quality_notes` | One-paragraph summary of the validation results, for people reading the record |
| `known_limitations` | Caveats a user must know, written by people (e.g. a survey that became biennial, a methodology change). From the source catalogue |
| `priority` | The team's priority for the dataset. From the source catalogue; empty (null) where not yet assigned |
| `validation` | Overall result (`passed`, `passed_with_warnings`) and every individual check with its status and details (see *Validation*) |
| `retrieval_history` | One entry per retrieval: time, checksum, raw file, source release time, whether the data changed, schema version and validation result |

The acquisition script fills in every field and refreshes the record each time it runs. Fields marked "From the source catalogue" are written by people in the catalogue CSV and copied as they are; nothing is added to a metadata record by hand, because the next run would overwrite it.

---

## Conventions

**Dates.** ISO 8601 (`YYYY-MM-DD`). Periodic data uses the first day of the period (August 2026 is `2026-08-01`, Q3 2026 is `2026-07-01`); the `frequency` field records the period length. Timestamps include the UTC offset.

**Missing values.** Left empty (null) in processed files, never `0` and never text such as "n/a" or "..". Statistics Canada uses status symbols (for example `..` for not available and `x` for suppressed); the processed table keeps these in a separate `status` column so "missing" and "suppressed" can be told apart. The Bank of Canada leaves a series out of a date when it has no value (market holidays), so no row is created for it: an absent row means "not published that day", not a missing value.

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
5. **Prepare:** analysis-ready tables are built from processed data.
6. **Review:** reproducibility, clarity and integration are then checked.

---

## Validation

Every retrieval is checked automatically before the processed file and metadata are written. Each check returns *pass*, *warn* or *fail*. A *fail* stops that table and leaves the previous processed file and metadata in place; the raw file is still kept for inspection. A *warn* is recorded and printed but does not stop the run.

| Check | What it looks at | Fails when |
|---|---|---|
| `schema` | Columns and dimensions compared with the previous retrieval | Never; a change is a warning and raises `schema_version` |
| `row_count` | Rows received compared with the number of data points the publisher reports | Never; a mismatch is a warning |
| `geography` | The requested geography exists in the table (case-insensitive, by name or DGUID) | The geography is not in the table |
| `dates` | Every reference period converts to an ISO date | Never; unrecognized formats are a warning |
| `numeric_values` | Every non-empty value is a number | Any value is not a number |
| `terminated_series` | Series the publisher no longer updates | Never; reported for information |
| `duplicates` | Repeated observations | Two rows share date, geography and series with different values. Exact copies are removed with a warning |
| `missing_values` | Every missing value has a status explaining it (`..`, `x`, `F`, `...`), and no published value carries one of those statuses | Never; unexplained or contradictory cases are a warning |
| `date_gaps` | Periods absent from the whole table, and series that skip periods inside their own date range (monthly, quarterly, semiannual and annual data). For daily, weekly and biweekly data: stretches longer than a week (two weeks, four weeks) without an observation | Never; gaps are a warning |
| `revisions` | The new processed file compared with the previous one: values that changed and observations that disappeared | Never; revisions are a warning, and the earlier version stays in its raw file |
| `freshness` | Whether the latest observation is as recent as the frequency and the usual publication lag allow (e.g. 75 days for monthly data) | Never; stale data is a warning |

The checks above `duplicates` that depend on the publisher's layout are Statistics Canada's. The Bank of Canada adapter runs its own: `series_present` (every requested series came back with values; fails otherwise), `units`, `future_dates` (fails on an observation dated after today), `terms` (the response carries the Bank's terms of use) and `empty_values`. Everything from `duplicates` down is shared by every publisher (`scripts/validation/hub_checks.py`).

To re-run the checks on processed files already on disk, without downloading:

```bash
python scripts/validation/validate_dataset.py --all
```

**Refresh readiness.** Before a scheduled refresh, check that the hub is in a good state, without downloading anything:

```bash
python scripts/validation/check_refresh_readiness.py
```

It confirms that every raw file still matches its recorded checksum, every processed file exists, no last validation failed, each catalogue entry has been acquired and each record is still in a catalogue, and it lists datasets due for a refresh. It exits with code 1 if anything would break the next refresh.

**Tests.** Run the automated tests with `python -m pytest tests`. They replace the publishers with fake services, so they need no network and no large download; GitHub Actions runs them on every push.

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

Acquisition instructions for each source are in `documentation/`: [statcan_acquisition.md](documentation/statcan_acquisition.md) and [boc_acquisition.md](documentation/boc_acquisition.md). How the hub lines up with the team's source catalogue and metadata work is in [integration_note_week3.md](documentation/integration_note_week3.md).

---

## Contributors

Risk Analytics Institute project team, Fall 2026.
