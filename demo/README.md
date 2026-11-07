# Demo: check the hub end to end in under a minute

This folder lets anyone (a mentor, a new team member, a reviewer) see the whole Canadian Risk Data Hub work, and check it, **without downloading anything**. That includes the 1.2 GB Labour Force Survey table. It runs every stage on small cuts of real Statistics Canada and Bank of Canada files, then compares every output with a stored copy of the expected result.

## Run it

From the repository root, with Python 3.12 or later:

```bash
pip install -r requirements.txt
python demo/run_demo.py
```

It takes a few seconds and ends with:

```
DEMO PASSED: 8 of 8 stages succeeded; all outputs match demo/expected/.
```

Add `--verbose` to see every stage's full output. The demo hub is built in `demo/output/`, which is recreated on every run. Your real `raw/`, `processed/` and `metadata/` folders are never touched.

To run the automated tests as well (the demo is one of them):

```bash
python -m pytest tests
```

## What it runs

| Stage | What happens | What it shows |
|---|---|---|
| 1. Acquisition, day 1 | Both publishers' acquisition scripts run. The sample files are served in place of Statistics Canada's and the Bank of Canada's services | Raw files stored as received; processed files with stable `series_id`; a full metadata record per dataset, validated against the team's QA standard |
| 2. Acquisition, day 2 | The same files again, one day later | Unchanged data is not copied again. Identical data on two different days makes the reproducibility rating **Confirmed High** |
| 3. Validation | Every processed file is re-checked | The same checks the acquisition ran |
| 4. Refresh readiness | Raw checksums, files and catalogues are checked | Nothing edited or missing |
| 5. Analysis-ready | The four analysis-ready tables are built and validated | Every row is traced back to a published observation, and derived monthly averages are recomputed |
| 6. Promotion readiness | The team's sign-off workflow is applied | What each dataset and table still needs before promotion (a reviewer's sign-off) |
| 7. Comparison | Every processed file, metadata record and analysis-ready table is compared byte for byte with `demo/expected/` | The hub gives the same result on any computer |

The dates are fixed (7 and 8 November 2026), so the outputs are identical on every computer and every day.

## What is in this folder

| Path | Contents |
|---|---|
| `run_demo.py` | The demo |
| `sample/` | Small cuts of real raw files (about 200 KB in total). Household spending: 8 categories, every survey year (60 rows). Labour Force Survey: the 4 unemployment-rate series, from 2024 (132 rows). Bank of Canada: the 4 datasets, from January 2026. Plus a small file per StatCan table standing in for its table-information service |
| `sources/` | The source catalogues the demo uses (copies of the hub's) |
| `expected/` | The outputs a correct run produces: processed files, metadata records, analysis-ready tables and their records |
| `make_samples.py` | How the samples were cut from the real raw files (for maintainers) |

The household sample deliberately includes the difficult cases:
- a total;
- categories that only appear in later survey years (cannabis, convenience fees);
- a category that skips a year;
- a category whose Canada values are all withheld as too unreliable ("F").

The analysis-ready household table built from it has exactly the 60 published rows. The old method of pivoting to a wide table and melting back added empty rows for the years a category did not exist (see `documentation/changelog.md`, v0.4).

## When the demo fails

- **A stage fails:** its output is printed under it. It is the same message the real scripts would give.
- **"outputs differ from demo/expected/":** the code now produces different results. If the change was intended, run `python demo/run_demo.py --update-expected`, review the changed files with `git diff demo/expected`, and commit them with the code change. If it was not intended, the difference points at what broke.

## Data and licences

The samples are subsets of data published by Statistics Canada (Open Licence: https://www.statcan.gc.ca/en/terms-conditions/open-licence) and the Bank of Canada (Terms of Use: https://www.bankofcanada.ca/terms/). Values are unchanged; only rows were removed. Source: Statistics Canada, tables 11-10-0222-01 and 14-10-0287-01; Bank of Canada, Valet series V39079, AVG.INTWO, BD.CDN.2YR/5YR/10YR/LONG.DQ.YLD and FXUSDCAD. Bank of Canada exchange rates are for statistical and analytical purposes only.
