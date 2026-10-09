# Canadian Risk Data Hub

A structured, repeatable process for identifying, acquiring, cleaning, maintaining and analyzing Canadian economic, financial and business-risk data.

Part of the Risk Analytics Institute project *Building an AI-Ready Canadian Risk Data Hub* (Oct 5 – Nov 2026).

> **Status:** Draft v0.1 (Week 1 prototype). Proposed architecture, open to revision after team design review.

---

## 1. Goals

- One consistent place for every dataset the team acquires.
- Every dataset documented with the same metadata, so it can be understood, trusted and reused by a new student, an RAI analyst, a future maintainer, or an AI application.
- Every acquisition reproducible: anyone can rerun a script and get the same raw data, and refresh it later.

The data lifecycle this repository supports:

```
Source identification -> evaluation -> repeatable acquisition -> raw storage
-> standardization -> documentation -> quality control -> analysis-ready data -> analytical/AI use
```

---

## 2. Repository structure

```
data_hub/
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
├── documentation/        # Methodology, update procedures, known limitations
├── outputs/              # Charts, tables and summaries produced from the data
├── requirements.txt      # Python dependencies
└── .gitignore
```

**Rules of thumb**

- `raw/` is never edited by hand. If something needs fixing, fix it in `scripts/processing/` so the change is reproducible.
- Large data files may be excluded from Git (see `.gitignore`). The script and the metadata record are what make them reproducible.

---

## 3. Raw vs. processed vs. analysis-ready data

| Stage | What it is | Allowed changes | Example |
|---|---|---|---|
| **Raw** | Data exactly as received from the provider | None. One file per retrieval, never edited | Original StatCan CSV with its original columns and symbols |
| **Processed** | Cleaned, standardized version of one raw dataset | Standardize dates, rename variables, convert numeric formats, harmonize units, remove duplicates, standardize missing values | Long-format table: `date, geography, series, value, unit, source, dataset_id` |
| **Analysis-ready** | Tables organized for a specific analytical use | Filtering, reshaping, joining datasets, derived measures | Monthly CPI and unemployment rate merged by province |

Each stage is generated from the previous one by a script, never by hand. This keeps the original source data preserved and every transformation reproducible.

---

## 4. Naming conventions

**Every dataset gets one ID**, built from four parts joined by underscores:

```
source _ table number _ subject _ frequency
```

Example: `statcan_18100004_cpi_monthly`

- `statcan`: who publishes it (`boc` for Bank of Canada, and so on)
- `18100004`: the publisher's own table number, so the data can always be traced back
- `cpi`: what it measures, in one or two words
- `monthly`: how often it is published (`daily`, `monthly`, `quarterly` or `annual`)

**Use that ID in every file that belongs to the dataset:**

| Stage | File name |
|---|---|
| Raw | `statcan_18100004_cpi_monthly_20261009.csv` |
| Processed | `statcan_18100004_cpi_monthly.csv` |
| Metadata | `statcan_18100004_cpi_monthly.json` |

Only raw files carry a date at the end. It is the day the data was downloaded (not the period the data covers), so each download is kept instead of overwritten.

**General rules:** all lowercase, underscores instead of spaces, and scripts start with a verb (`download_statcan_table.py`). Geography is left out of the ID because many tables cover several, and the stage (raw, processed) is shown by the folder.

---

## 5. Common metadata

Every dataset has one metadata record in `metadata/`. Fields marked **core** are required; the rest are optional or filled in automatically by the acquisition script.

| Field | Core | Description |
|---|---|---|
| `dataset_id` | ✔ | Internal unique identifier |
| `dataset_name` | ✔ | Human-readable name |
| `source_organization` | ✔ | e.g. Statistics Canada |
| `source_url` | ✔ | Page where the data is published |
| `source_table_or_series_id` | ✔ | Provider's table or series number |
| `description` | ✔ | What the data measures |
| `subject_category` | ✔ | inflation, labour, credit, housing, financial markets, etc. |
| `geography` | ✔ | Coverage and level (Canada, provinces, CMAs) |
| `frequency` | ✔ | Observation frequency |
| `observation_start` / `observation_end` | ✔ | First and last reference period |
| `units` | ✔ | Units and scaling (e.g. index 2002=100, $ millions) |
| `seasonal_adjustment` | ✔ | adjusted, unadjusted, or both |
| `retrieval_method` | ✔ | API, CSV download, bulk download |
| `retrieval_script` | ✔ | Script that fetches the data |
| `last_retrieved` | ✔ | Timestamp of the latest retrieval (auto-filled) |
| `raw_file_location` | ✔ | Path to the raw file |
| `processed_file_location` | ✔ | Path to the processed file |
| `update_frequency` | | How often the source publishes |
| `source_last_updated` | | Provider's own "last updated" date, if available |
| `checksum` | | SHA-256 of the raw file, to detect changes between retrievals |
| `licensing_access` | | Licence and access restrictions |
| `known_limitations` | | Revisions, suppression, classification changes |
| `quality_notes` | | Results of validation checks |

---

## 6. Conventions

**Dates.** ISO 8601 (`YYYY-MM-DD`). Periodic data uses the first day of the period (August 2026 is `2026-08-01`, Q3 2026 is `2026-07-01`); the `frequency` field records the period length. Timestamps include the UTC offset.

**Missing values.** Left empty (null) in processed files, never `0` and never text such as "n/a" or "..". Statistics Canada uses status symbols (for example `..` for not available and `x` for suppressed); the processed table keeps these in a separate `status` column so "missing" and "suppressed" can be told apart.

**Variable names.** Lowercase `snake_case`, no spaces, special characters or accents. Standard names for common columns: `date`, `geography`, `series`, `value`, `unit`, `source`, `dataset_id`, `status`.

**Numbers.** Plain numeric values, with no thousands separators or currency symbols. Units and scaling live in the `unit` column and the metadata.

**Encoding.** UTF-8 for all text files (French accents are common in Canadian sources).

---

## 7. Versioning and workflow

**Code and docs** are tracked in Git. Work on short-lived branches, and review before merging to `main`.

**Workflow**

1. **Acquire:** a script in `scripts/acquisition/<source>/` downloads data to `raw/`.
2. **Document:** the script writes or updates the metadata record in `metadata/`.
3. **Process:** a script in `scripts/processing/` writes the standardized table to `processed/`.
4. **Validate:** checks in `scripts/validation/` confirm the output and record notes in `quality_notes`.
5. **Prepare:** analysis-ready tables are built from processed data.
6. **Review:** reproducibility, clarity and integration are then checked.

---

## 8. Getting started

```bash
git clone https://github.com/soloadio/Canadian-Risk-Data-Hub
cd Canadian-Risk-Data-Hub
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Acquisition instructions for each source are added to `documentation/` as scripts are built. The Statistics Canada proof of concept is the first.

---

## 9. Contributors

Risk Analytics Institute project team, Fall 2026.
