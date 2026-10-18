# Canadian Risk Data Hub

A structured, repeatable process for identifying, acquiring, cleaning, maintaining and analyzing Canadian economic, financial and business-risk data.

Part of the Risk Analytics Institute project *Building an AI-Ready Canadian Risk Data Hub* (Oct 5 – Nov 2026).

> **Status:** Draft v0.2 (Week 2). The Statistics Canada prototype now has stable dataset IDs, versioned raw files and automated validation. See [documentation/changelog.md](documentation/changelog.md) for what changed.

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
├── sources/              # Catalogue of sources: publisher, access method, update cadence
├── raw/                  # Data exactly as received
│   └── <source>/<dataset_id>/
├── processed/            # Cleaned, standardized data
│   └── <source>/<dataset_id>/
├── analysis_ready/       # Final tables for analysis, dashboards, modelling, AI use
├── metadata/             # One metadata record per dataset
├── scripts/              # Acquisition, transformation and validation code
│   ├── acquisition/<source>/
│   ├── processing/
│   └── validation/
├── tests/                # Automated tests for the scripts (no network needed)
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
- `18100004`: the publisher's own table number, so the data can always be traced back
- `cpi`: what it measures, in one or two words
- `monthly`: how often the data is observed (`daily`, `weekly`, `biweekly`, `monthly`, `quarterly`, `semiannual`, `annual`, or `irregular` when no regular spacing is found)

**Use that ID in every file that belongs to the dataset:**

| Stage | File name |
|---|---|
| Raw | `statcan_18100004_cpi_monthly_20261009.csv` |
| Raw (table description from StatCan) | `statcan_18100004_cpi_monthly_20261009_cube_metadata.csv` |
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
| `licence` | Licence name and URL under which the data may be used (Statistics Canada: Open Licence) |
| `access` | How the data can be accessed: public or restricted, free or paid, account or key needed |
| `retrieval_method` | How the data is fetched: service, endpoint and file format |
| `retrieval_script` | Path of the script that fetched the data |
| `ai_use_note` | How AI tools were used in producing the dataset and what was validated. States that no data values were generated or changed by AI |

**Content**

| Field | Description |
|---|---|
| `subject_category` | inflation, labour, credit, housing, financial_markets, etc. Taken from the source catalogue (`sources/statcan_tables.csv`) and lowercased; `uncategorized` if left blank there |
| `geography` | Number of geographies in the source, and the coverage kept in the processed file (Canada by default; one or more places, or all, if requested) |
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
| `update_frequency` | How often the source publishes. Not detected yet, so left empty (null) |
| `source_last_updated` | Provider's own release time for the table, with UTC offset, if available |
| `checksum` | SHA-256 of the raw file, used to tell whether the data changed since the previous retrieval |
| `quality_notes` | One-paragraph summary of the validation results, for people reading the record |
| `validation` | Overall result (`passed`, `passed_with_warnings`) and every individual check with its status and details (see *Validation*) |
| `retrieval_history` | One entry per retrieval: time, checksum, raw file, source release time, whether the data changed, schema version and validation result |

The acquisition script fills in every field automatically and refreshes the record each time it runs.

---

## Conventions

**Dates.** ISO 8601 (`YYYY-MM-DD`). Periodic data uses the first day of the period (August 2026 is `2026-08-01`, Q3 2026 is `2026-07-01`); the `frequency` field records the period length. Timestamps include the UTC offset.

**Missing values.** Left empty (null) in processed files, never `0` and never text such as "n/a" or "..". Statistics Canada uses status symbols (for example `..` for not available and `x` for suppressed); the processed table keeps these in a separate `status` column so "missing" and "suppressed" can be told apart.

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
| `date_gaps` | Periods absent from the whole table, and series that skip periods inside their own date range (monthly, quarterly, semiannual and annual data) | Never; gaps are a warning |

To re-run the checks on processed files already on disk, without downloading:

```bash
python scripts/validation/validate_statcan_dataset.py --all
```

Run the automated tests with `python -m pytest tests`.

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

Acquisition instructions for each source are added to `documentation/` as scripts are built. The Statistics Canada proof of concept is the first: see [documentation/statcan_acquisition.md](documentation/statcan_acquisition.md).

---

## Contributors

Risk Analytics Institute project team, Fall 2026.
