# Guidebook

A plain-language tour of the whole system, then a glossary, then the questions a reviewer tends to ask.
No prior knowledge assumed.

## Part 1 - What this is for

A community health network runs twelve clinics and a mobile unit. Five outside systems send it files
every week:

- **the roster** - everyone registered with the network: name, date of birth, address, which clinic
  they belong to, who their doctor and care manager are, and their diagnoses
- **screenings** - short questionnaires patients fill in: the PHQ-9 (depression), the GAD-7 (anxiety)
  and a five-question social-needs check (housing, food, transport, utilities, safety)
- **lab results** - HbA1c (a three-month average of blood sugar, the key diabetes number) and LDL
  cholesterol
- **appointments** - from the clinics' scheduling system and the mobile unit's, which look different
- **pharmacy claims** - every prescription filled, and every one cancelled afterwards

The network wants to know, every week: which diabetic patients haven't had an HbA1c test, who screened
positive for depression and got no follow-up, who stopped refilling their statin, and whether any of
that differs by language, insurance, race or ethnicity. Care managers want a worklist. Engineers want
realistic data to build with, but must never have real patient records on their laptops.

Getting there means taking in files that are sometimes wrong, and being honest about it.

## Part 2 - The journey of one file

1. **It arrives.** A sensor notices a new file in the drop folder. The pipeline reads the date in its name
   (the *extract date*) and notes the day it arrived.
2. **It is copied and fingerprinted.** The file is copied into the *lake* - a folder nothing ever
   edits - and its SHA-256 fingerprint is recorded. If the same bytes arrive again under another name,
   the fingerprint gives it away and it isn't loaded twice.
3. **Its layout is checked.** The first line (the header) must exactly match a layout listed in the
   feed's *contract*. If the laboratory adds or renames a column without telling anyone, the file stops
   here with a report of what changed.
4. **It is loaded as text.** Every value goes into a *raw* table exactly as sent. Nothing is converted
   yet, so nothing is lost.
5. **File rules run.** Is it roughly the usual size? (A third of the usual size usually means it was cut
   off.) Which date format does it use?
6. **It is staged.** Values are typed: text becomes dates and numbers, supplier codes become the
   network's codes, lab units become standard units. A value that doesn't convert becomes blank, with
   the original kept beside it.
7. **Row rules run.** About a dozen per feed: is the date real, is the answer between 0 and 3, is the
   patient on the roster, is the code known? Each rule *repairs*, *holds*, *warns* or *stops*.
8. **Held rows wait in quarantine.** Some come back by themselves. If the patient was registered after
   the lab result arrived, next week's roster lets the result through.
9. **It is merged.** New rows are added and changed rows updated. For the roster, the old version is
   kept too, so the network can say which clinic a patient belonged to on any date.
10. **The gate decides.** Do the row counts add up? Is any file of this feed stopped? If all is well
    the feed *publishes*, and consumers see the new data all at once. If not, they keep seeing last
    week's.

## Part 3 - After publishing

- **dbt** builds the measures from the published tables and tests them.
- **Documents** are built: one per patient (a longitudinal record), one per clinic, one worklist per care
  manager.
- **De-identification** makes a demo copy and a flat extract, and the **leak gate** searches them for
  any real identifier before they are released.
- **Monitoring** writes the freshness report (on time? stuck?), updates the issue register, and sends a
  digest.

## Part 4 - Glossary

**Adherence (PDC).** Proportion of days covered: of the days from a patient's first fill of the year to
31 December, the share on which they had the medicine. At 80% or more a patient counts as adherent.

**Branch.** A step in a task graph that chooses which of the following steps run (load or skip,
publish or hold).

**Censored result.** A lab value reported as a bound, such as "<4.0" or ">14.0", because the machine
cannot measure beyond it.

**Change record.** A short document describing a supplier's change, what the pipeline does about it,
and how it was verified.

**Crosswalk.** The table linking each real value to its fake stand-in. It is the only way back, and it
never leaves the restricted area.

**Data contract.** A file describing exactly what a supplier sends: file names, columns, formats, who
owns it, how often it comes, and the rules it must pass.

**Data steward.** The person responsible for a feed's quality, who decides what to do with held rows.

**Date shift.** Moving all of one patient's dates by the same number of days, so the dates are fake
but the gaps between them are real.

**dbt.** A tool for writing data transformations as SQL files that depend on each other, with tests
attached.

**DAG (task graph).** A set of steps with arrows saying which must finish before which. "Directed
acyclic graph": the arrows never loop back.

**De-duplicated.** A row replaced by a later copy of the same record in the same run - usually an exact
copy sent twice in one file - and counted once.

**Direct identifier / quasi-identifier.** A direct identifier points to one person by itself (name,
record number, full date of birth). A quasi-identifier only does so in combination (sex + birth year +
ZIP3).

**DuckDB.** A database that lives in one file and runs inside the program: no server to install.

**Equity stratification.** Splitting a measure by group (language, insurance, race and ethnicity...) to
see who is being left behind.

**Freshness.** Whether data is as recent as it should be. Here: did the file arrive on time, and did it
make it to published?

**GAD-7 / PHQ-9.** Standard questionnaires for anxiety (7 questions) and depression (9 questions), each
answered 0-3. A PHQ-9 total of 10 or more is usually treated as a positive screen.

**Gate.** The check before publishing that can say HOLD.

**HbA1c.** A blood test showing average blood sugar over about three months. Reported in % (NGSP) or
mmol/mol (IFCC). 64 mmol/mol is 8.0%.

**Idempotent.** Doing it twice has the same effect as doing it once. Re-running a day changes nothing.

**Issue register.** The list of open problems. Each issue opens when a condition appears, ages while it
lasts, and resolves itself when the condition clears.

**Lake.** The folder where every supplier file is kept exactly as received.

**Leak gate.** The last check before de-identified data is released: it searches everything for real
identifiers.

**LOINC / ICD-10-CM.** Public code systems: LOINC for lab tests, ICD-10-CM for diagnoses.

**Natural key.** The columns that identify a row in the real world (a claim number and its status),
rather than a number the database made up.

**Quarantine.** Where rows that failed a rule wait, with the reason.

**Reconciliation.** Proving every row is accounted for: everything that came in went somewhere.

**Restatement.** A result already reported changes because late data arrived.

**Retryable rule.** A rule whose failure can fix itself, so the held row is re-checked on later runs.

**Row hash.** A fingerprint of a row's values, used to tell whether a row changed.

**Safe Harbor.** One of the two ways HIPAA allows data to count as de-identified: remove eighteen kinds
of identifiers.

**Sensor.** A step that waits for something to be true (a new file to arrive).

**Slowly changing dimension, type 2.** Keeping every version of a record with the dates it was valid,
instead of overwriting.

**Small-cell suppression.** Hiding counts that are small enough to point at individuals (here, 1-10).

**Staging.** Where this run's rows are typed and checked before they join the history.

**Surrogate.** A made-up value standing in for a real one.

**Trigger rule.** When a step in a task graph runs: only if all before it succeeded, or if at least
one did and none failed, or always.

## Part 5 - Questions a reviewer tends to ask

**Why not just drop bad rows?** Because a dropped row is invisible. A held row is counted, explained,
visible in the issue register, and often comes back on its own.

**Why stop a whole file instead of loading the good rows?** Because a file that is a third of its usual
size is almost never a third as much news. It is a broken extract, and publishing it would make
consumers believe two-thirds of the appointments were cancelled. If the supplier confirms the file
really is that small, a steward accepts it and it loads; the decision is recorded.

**How do you know the measures are right?** They are computed twice, by dbt and by independent Python
over the generator's own objects, and CI compares them at every clinic and, for adherence, for every
patient.

**What happens when a supplier changes its format?** The file stops with a report of the difference. A
change record is written and the new layout added to the contract. The first file under it archives
the existing data before loading.

**How would this scale?** The SQL is standard apart from a few functions. Moving from DuckDB to Postgres
or a cloud warehouse changes the connection and the file-loading step. Airflow runs the same task
graphs, and document builds already run in parallel.

**What would you add next?** Patient matching across feeds (the same person under two record numbers),
data-quality trend charts from `audit.rule_results`, and a small web view of the issue register for
stewards.
