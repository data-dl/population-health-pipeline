# De-identification

Two outputs leave the restricted zone, each under its own policy in [config/deid/](../config/deid/),
and neither leaves until the leak gate passes.

| output | policy | for | built from |
|---|---|---|---|
| demo copy of the patient, clinic and worklist documents | `demo_site` | engineers and trainers who need realistic data in a demo environment | the restricted documents |
| a flat patient-level extract | `safe_harbor` | analysis outside the care team | the warehouse |

Everything here runs on synthetic data. The policies show how a real deployment would be built. They
are not a legal determination.

## The demo copy (`demo_site`)

Pseudonymisation: one real patient becomes one fictional patient, and stays the same fictional patient on
every run.

| document path | becomes |
|---|---|
| `_id` (record number) | a random surrogate, `D` + 7 digits, remembered in the crosswalk |
| first and last name | a generated name that is not any real patient's or staff member's name |
| birth date, registration date, every screening, lab, visit and index date | shifted by the patient's own random 1-180 days, either direction |
| dates inside free text (care-gap details) | shifted by the same number of days |
| street | a generated street |
| ZIP | same first three digits, different last two (never the real ZIP) |
| phone | a generated 555 number |
| e-mail | built from the generated name, at example.org |
| care team names, care manager on worklists and clinics | generated names, consistent across documents |
| worklist patient references | the same surrogate as the patient's document |

Clinical values, codes and clinic identifiers stay. That's what makes a demo realistic. The one shift per
patient keeps every interval: a follow-up 30 days after a screening is still 30 days after it.

## The extract (`safe_harbor`)

Built for the HIPAA Safe Harbor method's list of identifiers:

| identifier | in the extract |
|---|---|
| names | not present |
| geography smaller than a state | ZIP3 only; 000 for low-population prefixes; clinic generalised to region |
| dates (except year) related to the person; ages over 89 | year of birth only; none for ages 90+, reported as "90+" |
| phone, fax, e-mail, URLs, IP addresses | not present |
| record numbers, health plan or account numbers, certificate or licence numbers | not present |
| vehicle or device identifiers, biometrics, photographs | not collected |
| any other unique identifying number or code | a random record id from a separate crosswalk, not derived from anything about the person |

Columns: record id, year of birth, age at year end, age band, sex, ZIP3, region, race and ethnicity,
language group, payer type, condition flags, seen this year, and for each measure whether the patient was
eligible and whether they met it.

## The leak gate

`pophealth/deid/verify.py` runs before anything is released. Every check must pass:

1. No real record number anywhere in the demo documents or the extract (8-digit tokens).
2. No real full name anywhere (word pairs in any string, not only name fields).
3. No real e-mail address anywhere.
4. No real phone number anywhere.
5. No real street address anywhere (as a run of words inside any string, not only address fields).
6. Every demo patient differs from its real patient in name, birth date, ZIP, phone and street.
7. Every patient's dates moved by one consistent, non-zero shift.
8. The demo policy transforms every document path registered as carrying an identifier
   (`PHI_PATHS` in `pophealth/documents/builders.py`, cross-checked with the contracts' classifications;
   a path whose column has no classification fails too).
9. References still resolve: patients to clinics, worklists to patients.
10. The extract carries only allowed columns.
11. No age above 89, and no birth year for anyone 90 or older.
12. Restricted ZIP3 prefixes are replaced by 000.

The report (`deid_verification.json` in the run folder) also lists quasi-identifier group sizes in the
extract. Safe Harbor doesn't set a minimum, but the table shows how identifying the remaining columns
are together. With year of birth, sex and ZIP3, most records sit in small groups. With age bands instead
of years, almost none do. That is the argument for generalising further before any release beyond the
network.

## The crosswalk

`restricted.deid_crosswalk` maps each real value to its surrogate, by kind: patient, name, date shift,
street, ZIP, phone, e-mail suffix, workforce name, extract record. It is the only way back from a
surrogate. It never leaves the warehouse's restricted schema and no output reads it. Surrogates come
from a seeded generator in order of first appearance: reproducible here, a secret seed in production.
