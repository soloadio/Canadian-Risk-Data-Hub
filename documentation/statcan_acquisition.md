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
11-10-0222-01,,household,
14-10-0287-01,,labour,
```

| Column | Required | What to put |
|---|---|---|
| `table_number` | Yes | The number on the table's StatCan page, e.g. `18-10-0005-01` |
| `subject` | No | One or two words for the dataset ID, e.g. `cpi`. If empty, it is built from the table title (place names such as "Canada" or "provinces" are left out) |
| `category` | No | A grouping label, e.g. `inflation`. Lowercased; `uncategorized` if empty |
| `geography` | No | Which place to keep in the processed file. Empty means Canada; `all` keeps every place |

Leave optional columns empty, but keep the commas.

## 2. Run the script

From the repository root:

```bash
python scripts/acquisition/statcan/download_statcan_table.py
```

Each table should print `SUCCESS` with its dataset ID. To run one table without editing the catalogue:

```bash
python scripts/acquisition/statcan/download_statcan_table.py --table 18-10-0005-01:cpi:inflation
```

Run `--help` for the other options (`--geography`, `--catalogue`, `--repo-root`).

## 3. What you get

For each table, named with its dataset ID (e.g. `statcan_18100005_cpi_annual`):

| Folder | File |
|---|---|
| `raw/statcan/<dataset_id>/` | `<dataset_id>_<YYYYMMDD>.csv`, the data exactly as received, plus `<dataset_id>_<YYYYMMDD>_cube_metadata.csv`, StatCan's description of the table |
| `processed/statcan/<dataset_id>/` | `<dataset_id>.csv` with columns `date, geography, series, value, unit, source, dataset_id, status` |
| `metadata/` | `<dataset_id>.json`, the metadata record described in the README |

Raw and processed CSVs are not committed to Git (see `.gitignore`); rerun the script to recreate them. The metadata records are committed.

## Rerunning

It is safe to run the script again. A rerun on the same day overwrites that day's raw file; a rerun on another day adds a new dated raw file. The output says whether the data changed since the last retrieval (compared using the checksum in the metadata record).

## Troubleshooting

- **HTTP 409:** StatCan locks tables while updating them (midnight to 8:30 a.m. Eastern). Try again after 8:30 a.m.
- **Network errors:** the script retries three times before reporting `FAILED`; the other tables still run.
- **Warning about frequency or dates:** the reference periods were not in a recognized format. Check the raw file and the `quality_notes` in the metadata.
- **Large tables:** some tables are several hundred MB (the Labour Force Survey table is about 1 GB raw), so the download can take a few minutes.
