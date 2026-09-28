# Runbook

How to operate the pipeline week to week, and what to do when it asks for something. Commands assume
`python -m pophealth` with `--workspace` and `--inbox` pointing at the deployment (omitted below).

## Every morning

1. Read the digest in the status or alerts channel, or the run's `summary.md`
   (`workspace/runs/<run id>/`). It opens with **Needs action** and **Watch**.
2. If Needs action is empty, you are done.
3. Otherwise work through the items below, then check `pophealth issues` for anything ageing.

## What each item means

| You see | It means | Do this |
|---|---|---|
| *publication held - X is blocked by APT-001 (135 rows against a usual 418)* | A file was far smaller than usual, most often a truncated extract. The feed keeps its last good publication | Ask the supplier to re-deliver. A re-delivered file for the same extract (`..._r1.csv`) supersedes the stopped one and the feed publishes on the next run. If the small file is genuinely complete (a holiday week), accept it: `pophealth accept <load id> --reason "..."` loads it on the next run. If it can never be fixed, `pophealth waive <load id> --reason "..."` lets the feed publish without it |
| *X has an unregistered layout - not loaded* | The header matches no layout in the contract. The shape report (`shape_diff_<file>.md` in the run folder) lists missing and unexpected columns | Confirm the change with the supplier. Write a change record (see [CR-004](change_records/CR-004-lab-layout-v2.md)), add the layout to the contract, and re-run. The file is still in the lake |
| *stopped by SCR-007 (item responses...)* | More than 5% of a file failed one rule: an upstream problem, not a few bad rows | Send the supplier the failing examples from `quarantine.csv` and request a corrected file. If the supplier confirms the file, `pophealth accept` loads it with the failing rows held one by one |
| *delivery late* (freshness LATE) | No file arrived by the date the contract expects | Contact the supplier. The issue resolves itself when a file arrives |
| *delivered but not published* (freshness STUCK) | A file arrived but is waiting, usually behind a stopped file in the same feed | Resolve the stopped file |
| *N late row(s) for the closed measurement year; results restated* | Rows for a year that has already been reported arrived (late lab results, claims adjudicated late) | Tell the people who use last year's measures. The Measures table shows old and new values |
| *layout vN -> vM recognised* | The first file under a newly registered layout loaded; clean data was archived first | Spot-check the first loads against the change record |
| *dbt build failed* | A dbt model or test failed. Measures and documents were not refreshed; the previous ones stand | `audit.dbt_results` has the failing node. Fix, then re-run the task: `pophealth task measures_and_documents dbt_build --as-of <day>` |
| *de-identified copy held - leak gate failed* | A real identifier would have left the restricted zone | **Never release by hand.** Read `deid_verification.json`, fix the policy or the builder, re-run. The previous release stands |

## Held rows (the steward's queue)

`pophealth issues` lists open issues by feed and rule. Each row in `quarantine.<feed>` carries the rules
it failed (`_q_rule_ids`) and why (`_q_detail`).

- **Retryable** rows (patient not on the roster yet, code not mapped yet) are re-checked whenever the
  feed loads new files, and whenever the roster or the reference data changes. They are released when
  they pass. Nothing to do unless they age.
- **Unmapped codes:** add the supplier's code to `config/reference/code_maps.yaml` (it is reviewed like
  code). Held rows with that code are re-staged from their raw text and released on the next run, new
  file or not.
- **Everything else** needs a correction from the supplier, or a steward's decision. `accept` (load a
  stopped file anyway) and `waive` (publish without it) are both recorded in `audit.decisions` with the
  reason and who decided.

## Re-running

- A whole day: `pophealth run --as-of <day>` (safe: tasks are idempotent). A run that stopped part-way
  is finished by the next one.
- One step: `pophealth task <dag> <task> --as-of <day>`, for example
  `pophealth task feed_labs curate --as-of 2026-03-03`.
- One document collection: `pophealth documents --collection worklists --as-of <day>`.

## Adding a feed

1. Write `config/contracts/<feed>.yaml`: sources and layouts, columns with classifications, rules,
   published tables, delivery cadence and freshness check.
2. Add `pophealth/feeds/<feed>.py` with the typing (`stage_select`), row-hash columns and curation, and
   register it in `pophealth/feeds/__init__.py`.
3. Add the feed to `feeds_order` in `config/pipeline.yaml`.
4. Add its published tables to `dbt/models/staging/_sources.yml` if dbt should read them.
5. Classify every identifier column. The leak gate refuses a document path built from an unclassified
   or uncovered identifier.
6. `pophealth docs` regenerates the dictionary, rule catalogue and lineage. A test fails until you do.

## Deploying on Airflow

See [airflow/README.md](../airflow/README.md): install the package, set `POPHEALTH_WORKSPACE`, create
the one-slot `pophealth_warehouse` pool, copy the DAG file.
