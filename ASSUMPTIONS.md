# Assumptions

Every default that was invented rather than given, where it lives, and why it was chosen. Change a
row's source and the generator, the pipeline and the tests follow.

## The network and its data

| Area | Assumption | Where | Why |
|---|---|---|---|
| Network | 12 clinics in four regions and one mobile unit that parks at host clinics; 5,000 patients | `config/reference/sites.csv`, `synth/world.py` | Big enough for rates to mean something per clinic, small enough to run in seconds |
| Clinic names, cities, ZIP codes | Tree names; ZIP3 prefixes 701-704 chosen arbitrarily; cities are placeholders | `config/reference/sites.csv` | Fictional on purpose |
| People | Names from common-name lists; phones in the fictional 555 exchange; e-mail at example.org | `synth/vocab.py` | Realistic shape, no real person |
| Deliveries | Weekly extracts on Mondays (2026-02-16, 02-23, 03-02); an extract carries what became available up to the day before | `synth/world.py` | A common supplier cadence |
| Measurement year | 2025, first reported from the 2026-02-16 extract; later 2025 rows restate it | `config/pipeline.yaml` | Makes restatement observable |
| Roster | Full snapshot each week; the warehouse keeps type 2 history | `config/contracts/roster.yaml` | Snapshots are how registration extracts usually arrive |
| Appointments | Two systems (clinic EHR scheduling and the mobile unit) with different layouts; incremental; a booking becomes completed, no-show or cancelled on its date | `config/contracts/appointments.yaml` | Two populations, one table |
| Laboratory | Layout v1 until the 2026-03-02 extract, v2 after it (renamed columns, explicit units, specimen id); part of the network reports HbA1c in IFCC mmol/mol | `config/contracts/labs.yaml`, `docs/change_records/CR-004-lab-layout-v2.md` | The most common kind of supplier change |
| Pharmacy | Paid and reversed claim rows; reversals up to ten days later; 40 claims for late 2025 adjudicated in February | `config/contracts/pharmacy.yaml` | Claims lag is real |

## Rules and thresholds

| Rule | Assumption | Where |
|---|---|---|
| Volume | A file below 0.5x (appointments) or 0.25x (labs, screenings, pharmacy) the median of the last four loads is stopped; not checked until the usual volume is at least 40 rows; the first load of an incremental feed (the backfill) is not a baseline. Roster snapshots are stopped below 0.9x | contracts |
| Tiered stop | Screening item errors: rows held while they are 5% of the file or less, the whole file stopped above that | `config/contracts/screenings.yaml` |
| Record numbers | Eight digits; shorter all-digit values are zero-padded and the repair recorded | contracts |
| Dates | Each file is parsed in whichever accepted format (ISO or US) reads more of its dates; a secondary format is a warning, not an error | contracts |
| Codes | Supplier codes map through `config/reference/code_maps.yaml`; an unmapped value is held (retryable) or defaulted to other/unknown where the contract says so | code maps |
| Race and ethnicity | One reporting category: Hispanic or Latino takes precedence; blank race with no Hispanic ethnicity is Unknown; "Multiple" becomes Multiracial. Completeness below 95% is a warning | `pophealth/feeds/roster.py` |
| Payer | Four categories: commercial, government, marketplace, self_pay (unknown when unmapped) | code maps |

## Measures

Simplified from common quality-measure definitions; the full wording is in
[dbt/seeds/measure_definitions.csv](dbt/seeds/measure_definitions.csv).

| Measure | Assumption |
|---|---|
| Active patient | At least one completed visit in the measurement year |
| Age | Whole years at 31 December of the measurement year |
| HbA1c | Diabetes means an E11 problem-list code; latest result in the year decides (ties broken by result id); controlled below 8.0%; poor control above 9.0% or no test |
| Depression screening | A complete PHQ-9 (all nine items) counts; an incomplete one does not |
| Follow-up | First complete PHQ-9 of 10 or more between 1 January and 1 December; a completed visit or another complete PHQ-9 in the following 30 days (day 30 counts, day 31 does not) |
| Adherence (PDC) | Two or more fills on different days in the year; treatment period from the first fill to 31 December; an early refill of the same drug starts when the previous supply ends; different drugs in a class cover the same days once; no carry-in from the prior year; adherent at 80% or more, compared in whole numbers |
| Social needs | All five domains answered; "declined" counts as answered |
| Small cells | A stratified cell with a denominator below 11, or a numerator or its complement from 1 to 10, is suppressed and shows no counts at all; if that leaves one suppressed group in a stratifier, the next smallest is suppressed too |

## De-identification

| Area | Assumption |
|---|---|
| Demo copy | Every date for a patient moves by one random shift of 1-180 days, either direction; names, streets, phones, e-mails and ZIP codes replaced; ZIP keeps its first three digits; workforce names replaced |
| Surrogates | Drawn from a seeded generator in order of first appearance (a secret seed in production), remembered in `restricted.deid_crosswalk`; separate surrogates for the demo copy and the extract |
| Safe Harbor extract | Year of birth only, none for ages 90 or older (reported as 90+); ZIP3, with prefix 704 treated as a low-population area for the demo; clinic generalised to region |
| Group sizes | Reported for (birth year, sex, ZIP3) and (age band, sex, ZIP3) at a threshold of 11; informational |

## Code systems

LOINC 4548-4 (HbA1c, %), 59261-8 (HbA1c, IFCC mmol/mol), 13457-7 (LDL cholesterol, calculated),
44261-6 (PHQ-9 total) and 70274-6 (GAD-7 total); ICD-10-CM E11.x, I10, E78.x, F32.x, F33.x; generic drug
names only. The IFCC-to-NGSP conversion is NGSP % = 0.09148 x IFCC + 2.152, rounded half up to one
decimal. PHQ-9 and GAD-7 are used for their scoring rules only; no question text appears.
