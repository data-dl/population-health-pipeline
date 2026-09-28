# Design decisions

Each decision: what was chosen, why, and what it costs. The ones marked *learned* changed because a
test, or the second implementation, caught something.

## Ingestion

### 1. Contracts are configuration; logic is code

Everything that varies by supplier lives in one YAML contract per feed: file-name pattern, every layout
the supplier has used (source column to canonical column), date formats, natural key, column
classification, steward, schedule and rules. The code that types values and curates tables lives in
one small module per feed, and everything else is generic.

*Why:* a supplier change becomes a reviewable config diff with a change record
([CR-004](change_records/CR-004-lab-layout-v2.md)), not a code change hidden in a script. The data
dictionary, rule catalogue and lineage are generated from the same files, so they cannot drift.
*Cost:* rule expressions are SQL strings inside YAML. They are tested through the scenario, not in
isolation.

### 2. Raw is text, and immutable

Files are copied into a lake folder and registered by SHA-256 before anything reads them. Their rows
land in a raw table per layout, every column as text, plus a row number. Nothing downstream edits raw.

*Why:* any later fix (a new code mapping, a corrected parser) can be re-applied to the original values.
Re-staging held rows depends on it (decision 5). A byte-for-byte resend under a new name is recognised
by its checksum and not loaded twice. *Cost:* storage. That's trivial here and cheap anywhere.

### 3. A file's layout is matched by its header, never by position

A header that matches no registered layout stops the file. The run writes a shape report naming the
missing and unexpected columns against the closest layout. The first file under a new registered
layout archives the clean table before loading.

*Why:* loading by position is how a renamed column silently becomes the wrong data. *Cost:* every
legitimate change needs a contract update first. That is the point.

### 4. Every rule declares its action, and some escalate

Actions are `repair`, `quarantine`, `warn` and `block`. A quarantine rule can set `block_if_rate_over`:
the screening item rule holds a handful of bad rows, but stops the whole file when more than 5% fail,
because at that point something upstream is broken and loading the rest would hide it.

*Why:* one bad row and a broken export need different responses, and the contract should say which is
which.

### 5. Hold rows instead of dropping them; re-check what can resolve itself

A failing row goes to `quarantine.<feed>` with the ids of the rules it failed. If every rule it failed
is retryable (patient not on the roster yet, code not mapped yet), it is re-checked whenever the feed
loads new files, and whenever the roster or the reference data has changed since its last check.
Otherwise it waits for a steward.

*Learned:* re-checked rows were first copied back from quarantine, so they carried the code mapping
from their first staging. Adding the mapping could never release them. Held rows are now re-staged from
their raw text, so whatever changed since applies (`test_a_code_mapping_added_later_releases_held_rows`).

*Learned:* the re-check was first triggered by "the roster published in this run", compared by run id.
On Airflow every DAG run has its own id, so it never fired there, and a new code mapping triggered
nothing anywhere until the feed's next file. Each feed now records what its held rows were last checked
against - the published roster's content hash and the reference data's hash - and re-checks when either
differs (`test_feed_dags_run_through_the_airflow_entry_points`).

### 6. Date formats are detected per file

The contract lists the accepted formats. For each file the pipeline counts how many dates parse under
each, uses the winner for the whole file, and warns when it isn't the primary format.

*Why:* guessing per value is how 02/03 becomes 3 February in one row and 2 March in the next. One format
per file is almost always the truth.

### 7. Integers are validated as text first

*Learned:* DuckDB's `try_cast('2.5' as integer)` returns 3 rather than failing, so a fractional PHQ-9
answer would have been accepted as a whole number. Item responses and days-supply are checked against
`-?[0-9]+` before casting (`whole_number()` in `pophealth/feeds/base.py`).

### 8. Units are converted only when known, in exact decimal arithmetic

HbA1c in IFCC mmol/mol becomes NGSP % with `round(x * 0.09148 + 2.152, 1)` on DECIMAL (half away from
zero). An unknown unit gets no standard value and its row is held. Censored results (`<4.0`, `>14.0`)
keep the bound and a censor mark.

*Why:* a result in the wrong unit is worse than a missing one. Binary floating point can move a value
that sits exactly on a rounding boundary.

## Warehouse

### 9. Layers with one job each

raw (as sent), staging (this run, typed, with rule outcomes), clean (merged history), curated (derived,
rebuilt whenever the feed loads), published (what consumers read), plus quarantine, audit, archive, ref
and restricted.
dbt reads only published.

*Why:* each question has one place to ask it. What did the supplier send? Raw. Why was this row held?
Quarantine and `audit.row_findings`. What did consumers see on Tuesday? The publish log and content
hashes.

### 10. Merges by natural key with row hashes; type 2 history for the roster

Incremental feeds upsert by natural key. A row hash over the business columns sorts each row into
inserted, updated, unchanged or de-duplicated (exact duplicates inside a file). Two different versions
of one key inside one file are a conflict. Both are held: neither is guessed. The roster is a weekly
snapshot kept as type 2 history, and a patient missing from a snapshot is closed. A patient whose row was
held keeps their last good version.

### 11. Reconcile every file

For every file merged in a run: raw = staged + malformed, staged = passed + held,
passed = inserted + updated + unchanged + de-duplicated. The gate will not publish a feed whose counts
don't close. The counts are in every run's `reconciliation.csv`.

*Learned:* the de-duplicated count was first derived as the remainder (passed minus inserted, updated and
unchanged), so the third identity could never fail. Every count is now measured on its own.

### 12. Publish all or nothing, and never past a gap

The gate decides per feed. A feed with a stopped file anywhere in its history publishes nothing until
that file is re-delivered (a later file for the same extract supersedes it), or a steward decides:
`pophealth accept` loads the file anyway, overriding the rule that stopped it, and `pophealth waive`
publishes without it. Both are recorded in `audit.decisions` with the reason. In run 3 the mobile unit's
file loaded cleanly but waited for the clinic file's re-delivery, so appointments never published half a
week.

*Learned:* at first the only decision was a waiver, and the runbook told stewards to waive a small file
that was genuinely complete - which would have skipped that week's rows while looking like acceptance.
Accepting and skipping are now separate commands.

*Cost:* one bad file delays good ones. The freshness report makes that visible (STUCK) instead of
silent.

### 13. DuckDB, one file

The warehouse is a single DuckDB file. It's fast enough for millions of rows, needs no server, and
behaves the same on a laptop and in CI.

*Cost:* one writer at a time. Locally, tasks run in sequence. On Airflow every task takes a one-slot
pool. At larger scale the same SQL moves to Postgres or a cloud warehouse. What would change is the
connection class and a few dialect functions (`try_strptime`, `regexp_full_match`, `UNPIVOT`).

## Orchestration

### 14. Task graphs as data; tasks share state only through the warehouse

DAGs are plain definitions (`pophealth/orchestration/dags.py`) naming callables as strings, so parsing
them imports nothing heavy. Tasks never pass data in memory. Each reads what it needs from `audit.files`,
`audit.loads`, staging and quarantine, so any one can be re-run alone (`pophealth task`) and a run that
dies halfway resumes where it stopped.

The in-process runner implements Airflow's semantics: trigger rules, branch skips, soft-failing
sensors, retries and triggers. The Airflow adapter maps the same definitions onto real operators.

Each feed's sensor waits for work, read from that durable state: a file not seen before, a load an
earlier run left part-way, merged rows free to publish now that a stopped file was re-delivered or
waived, or held rows to re-check. With none of these, the DAG stands down at the sensor.

*Learned:* the first sensor was satisfied by any file in the drop, old ones included, and the branch
after it looked only for newly landed files. A run that stopped after loading was not finished until the
next delivery, and a waiver did not publish until then either. Tests now stop a run part-way, waive a
file and accept one, and require the next run to finish the job.

### 15. Downstream runs only when published content changed

Each publish records a content hash per table. A feed triggers measures and documents only if a hash
changed, so a same-day re-run, or a week with nothing new, does no downstream work.

### 16. Order is not a correctness requirement

The roster runs first so fewer rows wait, but a clinical feed that runs before it is still correct: its
rows for unknown patients are held and released once the roster publishes them, including on Airflow,
where each DAG runs on its own schedule.

## Measures

### 17. Measures in dbt, graded against a second implementation

The measures are dbt models with their own tests: accepted values, relationships, ranges, clinic rows
summing to the network row, and suppression invariants. `synth/oracle.py` computes the same measures
from the generator's truth objects in plain Python loops, sharing no code with dbt. CI requires the two
to agree at every clinic, and for adherence patient by patient.

### 18. Adherence without recursion

Carrying an early refill forward is naturally recursive: each fill starts at the later of its own date
and the end of the previous supply. Unrolled, the start of fill *i* is
`C_i + max over j <= i of (f_j - C_j)`, where `C` is the supply dispensed before a fill and `f` the fill
day. That's a running maximum, so it takes two window passes
([int_pdc.sql](../dbt/models/intermediate/int_pdc.sql)). The oracle uses the plain loop, and CI shows
they agree. The 80% threshold is compared as `5 x covered >= 4 x days`, so no float can move a patient
across the line.

### 19. Equity views with suppression built in

Every measure is stratified by race and ethnicity, language, payer, sex and age band. Each group gets its
rate, its rate relative to everyone else in the stratifier, and its share of the eligible population and
of those meeting the measure (a composition view). A cell with fewer than 11 patients, or a numerator or
complement from 1 to 10, is suppressed and shows no counts at all. If that leaves exactly one suppressed
group in a stratifier, the next smallest is suppressed too, so the hidden one cannot be recovered from
the total. Two dbt tests enforce it.

*Learned:* suppressed cells first still showed their denominators when these were 11 or more. With the
network total published beside them, a small group's size could be recovered by subtraction. A
suppressed cell now shows nothing, and the dbt test checks every column.

### 20. Late data restates, and says so

Rows for the closed measurement year that arrive later (late lab results, late-adjudicated claims) are
announced by a timeliness rule, and each run compares its measures with the previous run's. In run 2,
three measures change. The summary marks them restated.

## Documents

### 21. A collection registry; clinics in parallel

Each document collection names its builder, key, partition and the tables it reads (its lineage) in
`config/collections.yaml`. Patient documents are built one clinic per worker, each with its own DuckDB
cursor. One collection can be rebuilt alone for troubleshooting. The store upserts by `_id` and reports
inserted, updated, unchanged and deleted, the same counts a MongoDB bulk upsert would give.

*Learned:* worklists were first partitioned by clinic too. Patients who move clinic keep their care
manager, so one care manager's worklist was built twice under one id and the second copy overwrote the
first, losing 909 open gaps. A test comparing the gaps in the documents with the gaps in the mart found
it, and worklists are now built in one pass. A worklist lists every clinic its patients attend.

*Learned, again:* `--sites` still narrowed every collection, so a partial rebuild would have replaced a
care manager's worklist with part of it. Only a collection built clinic by clinic can now be built for
some clinics; the others are always built whole.

## De-identification

### 22. Pseudonyms from a crosswalk, not from the values

Surrogates are drawn from a seeded generator in order of first appearance and remembered in
`restricted.deid_crosswalk`, never computed from the real value. A hash of a record number is still
derived from it. The demo copy and the Safe Harbor extract use separate crosswalks, so they cannot be
joined. Generated names are checked against every real patient and staff name.

*Learned:* the first version required every surrogate kind to be unique. That's right for identifiers,
impossible for a date shift (360 values, 5,000 patients). Uniqueness is now per kind.

### 23. One date shift per patient

Every date in a patient's documents moves by the same number of days. A follow-up 30 days after a
screening is still 30 days after it in the demo copy. The leak gate checks each patient's shift is
consistent and non-zero, and a test checks every interval.

### 24. The leak gate searches text, not fields

The gate collects every string in the demo documents and the extract and searches them for real record
numbers, phone numbers, e-mail addresses, street addresses (as runs of words) and full names (as word
pairs). It doesn't trust that identifiers only appear where they are expected. It also checks that the
policy transforms every registered identifier path - and that each path's column is classified at all -
that references still resolve, and that the extract has only allowed columns, capped ages and restricted
ZIP3s. Nothing is released unless every check passes. Tests show it fails on a planted leak, on a policy
missing one field, and on an unclassified column.

*Learned:* street addresses were first matched only as whole field values, so an address written into a
note would have passed. They are now searched inside every string, like the other identifiers.

## Monitoring

### 25. Freshness in two parts

Per supplier source: did the delivery arrive when the contract says (ON_TIME, DUE, LATE)? And did what
arrived reach published (OK, or STUCK with the reason)? Beside that, the newest event delivered against
the newest event published. A feed can be on time and stuck, as appointments were in run 3.

### 26. An issue register that manages itself

Every run rebuilds the list of open conditions: held rows per feed and rule, stopped files, late
deliveries. It then opens new issues, updates counts and ages on open ones, and resolves those whose
condition cleared, recording why (rows released or superseded, file re-delivered, delivery arrived).

### 27. Two channels, masked text

Status goes to one channel, anything needing action to another. A webhook is posted only if configured.
Summaries, issue details and run health mask long digit runs, so a record number never reaches a chat
message or a log.

## The second language

### 28. The screening rules in R as well

[r/score_screenings.R](../r/score_screenings.R) implements the file-level screening rules and scoring in
data.table, independently of the SQL. CI runs both on the same 3,000-row file and requires the same
rejected rows for the same rules and the same scores.

*Learned:* the first R run rejected hundreds of valid rows. R's YAML parser read the unquoted
social-needs answers `Y` and `N` as true and false; Python's reads them as text. The values are now quoted,
with a comment saying why.

## Testing

### 29. A generator with an answer key

The synthetic network plants 269 defects of 26 kinds and records each one's file, row, rule and final
state. Tests grade both recall (every planted defect caught by its rule) and precision (nothing else
held, repaired or warned), plus merge counts, file outcomes and measures.

*Learned:* the pipeline's item-range rule caught the generator itself putting PHQ-9 answers above 3.
The generator was fixed and now has its own validity test.

*What it can't show:* data-entry patterns no one thought to plant. The rules are general (ranges,
formats, references, keys), not keyed to the planted cases, and the precision test keeps them from being
tuned to the plant.
