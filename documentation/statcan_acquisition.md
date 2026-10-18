# Statistics Canada acquisition

How to download Statistics Canada tables into the hub with `scripts/acquisition/statcan/download_statcan_table.py`.

## Before you start

Set up the environment as described in the README (*Getting started*), then activate it:

```bash
source .venv/bin/activate        # Windows: .venv\Scripts\activate
```

## 1. Add the table to the catalogue

Every table the hub tracks is listed in `sources/statcan_tables.csv`, one row per table:

```
table_number,subject,category,geography
11-10-0222-01,household_spending,household,
14-10-0287-01,labour_force_characteristics,labour,
```

| Column | Required | What to put |
|---|---|---|
| `table_number` | Yes | The number on the table's StatCan page, e.g. `18-10-0005-01` |
| `subject` | Recommended | One or two words for the dataset ID, e.g. `cpi`. If empty, it is built from the table title (place names such as "Canada" or "provinces" are left out). Only used the first time: once a table has a metadata record, its ID is kept |
| `category` | No | A grouping label, e.g. `inflation`. Lowercased; `uncategorized` if empty |
| `geography` | No | Which place to keep in the processed file. Empty means Canada; `all` keeps every place; separate several with `;` (e.g. `Ontario;Quebec`). Matching ignores capitals, and a DGUID also works. A name that is not in the table stops the run with the list of valid names |

Leave optional columns empty, but keep the commas.

## 2. Run the script

From the repository root:

```bash
python scripts/acquisition/statcan/download_statcan_table.py
```

Each table should print `SUCCESS` with its dataset ID and the validation result, followed by any warnings. To run one table without editing the catalogue:

```bash
python scripts/acquisition/statcan/download_statcan_table.py --table 18-10-0005-01:cpi:inflation
```

Run `--help` for the other options (`--geography`, `--catalogue`, `--repo-root`).

## 3. What you get

For each table, named with its dataset ID (e.g. `statcan_18100005_cpi_annual`):

| Folder | File |
|---|---|
| `raw/statcan/<dataset_id>/` | `<dataset_id>_<YYYYMMDD>.csv`, the data exactly as received, plus `<dataset_id>_<YYYYMMDD>_cube_metadata.csv`, StatCan's description of the table. A second version on the same day is saved as `..._<YYYYMMDD>_2.csv` |
| `processed/statcan/<dataset_id>/` | `<dataset_id>.csv` with columns `date, geography, series, value, unit, source, dataset_id, status` |
| `metadata/` | `<dataset_id>.json`, the metadata record described in the README, including the validation results and the retrieval history |

Raw and processed CSVs are not committed to Git (see `.gitignore`); rerun the script to recreate them. The metadata records are committed.

## Rerunning

It is safe to run the script again.

- **The dataset ID never changes.** If the table already has a metadata record, its ID is reused, even if StatCan renames the table or changes how often it publishes. The output says so when the ID it would have built is different.
- **Raw files are never overwritten.** If the data is identical to the last stored version (same SHA-256 checksum), no new copy is written, which matters for the 1 GB Labour Force Survey table. If the data changed, a new dated file is added, with a counter if one already exists for that day.
- **Every run is logged.** Each retrieval is added to `retrieval_history` in the metadata record, with its checksum, raw file, StatCan release time and validation result. Records from Week 1 start their history with the Week 1 retrieval.

The output says whether the data changed since the last retrieval.

## Validation

The checks are described in the README (*Validation*). In short:

- A **failed** check (unknown geography, values that are not numbers, conflicting duplicate rows) prints `FAILED`. The processed file and metadata record keep their previous version. The raw file is kept so the problem can be inspected.
- A **warning** (schema change, row count different from what StatCan reports, gaps in the dates, missing values without a status) is printed and recorded under `validation` in the metadata record.

To re-check processed files already on disk:

```bash
python scripts/validation/validate_statcan_dataset.py --all
```

### Known warnings for the current tables

- **Household spending (11-10-0222-01):** the Survey of Household Spending became every second year after 2017, so 2018, 2020 and 2022 are absent from the whole table. One series ("Collectors' items") also skips a year. These are features of the source, not download errors. The dataset keeps the frequency `annual`, as StatCan declares it.

## Troubleshooting

- **HTTP 409:** StatCan locks tables while updating them (midnight to 8:30 a.m. Eastern). Try again after 8:30 a.m.
- **Network errors:** the script retries three times before reporting `FAILED`; the other tables still run.
- **Warning about dates:** some reference periods were not in a recognized format. Check the raw file and the `validation` section of the metadata record.
- **Geography not found:** the output lists the geographies the table has. Fix the spelling in the catalogue or use `--geography`.
- **Schema changed:** StatCan added, removed or renamed a column or dimension. The `schema_version` goes up. Check that notebooks still find the series they use.
- **More than one metadata record for a table:** an ID was renamed by hand without removing the old record. Keep the one in use.
- **Large tables:** some tables are several hundred MB (the Labour Force Survey table is about 1 GB raw), so the download can take a few minutes.
