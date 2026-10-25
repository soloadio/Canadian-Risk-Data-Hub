# Bank of Canada acquisition

How to download Bank of Canada series into the hub with `scripts/acquisition/boc/download_boc_series.py`. The script uses the Bank's Valet API, which is public, free and needs no account or key.

## Before you start

Set up the environment as described in the README (*Getting started*), then activate it:

```bash
source .venv/bin/activate        # Windows: .venv\Scripts\activate
```

## 1. Add the series to the catalogue

Every Bank of Canada dataset the hub tracks is a row in `sources/boc_series.csv`:

```
series_ids,id_part,subject,name,category,update_frequency,refresh_schedule,revision_policy,priority,known_limitations
V39079,,policy_rate,,interest_rates,"business daily; ...","11:30 ET on business days (CRON_TZ=America/Toronto)",Not revised,high,"A change announced at 09:45 ET takes effect the next business day. ..."
BD.CDN.2YR.DQ.YLD;BD.CDN.5YR.DQ.YLD;BD.CDN.10YR.DQ.YLD;BD.CDN.LONG.DQ.YLD,bdcdnyld,bond_yields,"Selected benchmark bond yields: ...",interest_rates,...
```

| Column | Required | What to put |
|---|---|---|
| `series_ids` | Yes | One Valet series name, or several separated by `;` to keep them in one dataset (they must share a frequency). Find names at `https://www.bankofcanada.ca/valet/lists/series/json` |
| `id_part` | When there are several series | The publisher-ID part of the dataset ID. For one series it defaults to the name without punctuation (`AVG.INTWO` gives `avgintwo`) |
| `subject` | Yes | One or two words for the dataset ID, e.g. `corra`. Only used the first time: once a dataset has a metadata record, its ID is kept |
| `name` | No | A readable name. For one series the Bank's label is used; recommended when there are several |
| `category` | No | A grouping label, e.g. `interest_rates`. Lowercased; `uncategorized` if empty |
| `update_frequency`, `refresh_schedule`, `revision_policy`, `priority`, `known_limitations` | No | Written by people, copied into the metadata record as they are (see the README, *Common metadata*). Quote a value that contains commas |

The four datasets in the catalogue, and the values in their descriptive columns, follow Maurya Shah's Bank of Canada integration specification (W3-MAU-01).

## 2. Run the script

From the repository root:

```bash
python scripts/acquisition/boc/download_boc_series.py
```

Each dataset prints `SUCCESS` with its dataset ID and validation result. To run some datasets only, name their subjects:

```bash
python scripts/acquisition/boc/download_boc_series.py --only policy_rate --only exchange_rate
```

The whole catalogue takes a few seconds; the four raw files total about 4 MB.

## 3. What you get

| Folder | File |
|---|---|
| `raw/boc/<dataset_id>/` | `<dataset_id>_<YYYYMMDD>.json`, the Valet response exactly as received (full history of every series, with the Bank's labels, units and terms link). A second version on the same day is saved as `..._<YYYYMMDD>_2.json` |
| `processed/boc/<dataset_id>/` | `<dataset_id>.csv` with columns `date, geography, series, value, unit, source, dataset_id, status`. `series` is the Bank's label, e.g. `Benchmark bond yield, 10-year` |
| `metadata/` | `<dataset_id>.json`, the metadata record described in the README |

Raw JSON and processed CSV files are not committed to Git (see `.gitignore`); rerun the script to recreate them. The metadata records are committed.

## How the data is handled

- **Full history on every run.** The script asks for each series' whole history, not the last few observations, so the raw file is always the complete published series and its checksum shows whether anything changed, including revisions to old values. It never uses Valet's `recent=n` option, which counts observations per series and so returns misaligned dates when series publish on different days.
- **Holidays.** On market holidays the Bank leaves the market series (CORRA, yields, USD/CAD) out of that date instead of publishing an empty value, while the policy rate is still published. The processed file only has rows where the Bank published a value, so holidays create no empty rows and never overwrite a value with a blank.
- **Revisions.** If a later download changes an earlier value (CORRA can be republished the same day before the Bank's cut-off), the old raw file is kept, a new one is written, and the `revisions` check lists what changed.
- **One key per observation.** `date`, `geography` and `series` identify an observation; two rows with the same key and different values fail the `duplicates` check.

## Rerunning and scheduling

It is safe to run the script again. As for Statistics Canada, the dataset ID never changes once assigned, raw files are never overwritten, an unchanged response is not copied again, and every run is logged in `retrieval_history`.

The `refresh_schedule` column records when each dataset should be refreshed. The hub does not run on a server yet, so nothing is scheduled automatically. To schedule it on a Linux or macOS machine, give cron the Eastern time zone explicitly, or 11:30 runs at the server's local time:

```
CRON_TZ=America/Toronto
30 11 * * 1-5  cd /path/to/Canadian-Risk-Data-Hub && .venv/bin/python scripts/acquisition/boc/download_boc_series.py
15 17 * * 1-5  cd /path/to/Canadian-Risk-Data-Hub && .venv/bin/python scripts/acquisition/boc/download_boc_series.py --only exchange_rate
```

On Windows, create the same two tasks in Task Scheduler (weekdays at 11:30 and 17:15, on a computer set to Eastern time). Running `python scripts/validation/check_refresh_readiness.py` first confirms the stored files are intact and lists datasets that are due.

## Validation

The shared checks are described in the README (*Validation*). The Bank of Canada adapter adds:

| Check | Fails when |
|---|---|
| `series_present` | A requested series is missing from the response or has no values |
| `future_dates` | An observation is dated after the day of the download |
| `units` | Never; series in one dataset with different or missing units are a warning |
| `terms` | Never; a response without the terms-of-use link is a warning |
| `empty_values` | Never; observations carrying an empty value are left out with a warning |

## Licence

Bank of Canada content may be used, copied and distributed freely, provided the Bank is credited as the source and any changes are indicated ([Terms of Use](https://www.bankofcanada.ca/terms/)). The Bank says its exchange rates are for statistical and analytical purposes only, not for executing trades. The terms also forbid getting around the Bank's request limits; the script makes one request per dataset.

## Troubleshooting

- **"does not recognize this series":** the series name in the catalogue is wrong. Check it in the series list linked above.
- **"did not return JSON":** the Valet service is probably down; the script retries three times. Try again later.
- **"different frequencies":** a row lists series with different frequencies. Put them in separate rows.
- **Schema changed:** a series was added to or removed from a row. The `schema_version` goes up. Check that notebooks still find the series they use.
