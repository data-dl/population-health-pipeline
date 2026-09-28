# Run 2026-03-03_01 - as of 2026-03-03

**1 item(s) need action** - 5 feeds checked - 1 file(s) loaded - measures refreshed - de-identified copy released

## Needs action

- **pharmacy** (pharmacy_claims): delivery late - expected by 2026-03-02, 1 day(s) late

## Feeds

| feed | files | rows in | held | repaired | warned | gate | published |
|---|---|---|---|---|---|---|---|
| roster | nothing new | 0 | 0 | 0 | 0 | - | not run |
| screenings | nothing new | 0 | 0 | 0 | 0 | - | not run |
| labs | nothing new | 0 | 0 | 0 | 0 | - | not run |
| appointments | appointments_main_20260302_r1.csv (published) | 387 | 0 | 0 | 0 | PUBLISH | appointments |
| pharmacy | nothing new | 0 | 0 | 0 | 0 | - | not run |

## Freshness

| feed | source | delivery | pipeline | newest delivered | newest published | days behind | detail |
|---|---|---|---|---|---|---|---|
| appointments | clinic_scheduling | ON_TIME | OK | 2026-03-01 | 2026-02-27 | 2 |  |
| appointments | mobile_unit | ON_TIME | OK | 2026-03-01 | 2026-02-27 | 2 |  |
| labs | reference_lab | ON_TIME | OK | 2026-02-27 | 2026-02-27 | 0 |  |
| pharmacy | pharmacy_claims | LATE | OK | 2026-02-22 | 2026-02-22 | 0 | expected by 2026-03-02, 1 day(s) late |
| roster | ehr_roster | ON_TIME | OK | 2026-03-02 | 2026-03-02 | 0 |  |
| screenings | screening_platform | ON_TIME | OK | 2026-02-27 | 2026-02-27 | 0 |  |

## Rules that fired

| feed | rule | action | rows (new) | rows re-checked | what it checks |
|---|---|---|---|---|---|
| appointments | APT-006 | quarantine | 0 | 6 | Status code is mapped |
| appointments | APT-009 | quarantine | 0 | 3 | Patient is on the roster |

## Issue register

1 opened, 1 resolved this run; 18 open.

| issue | since | age (days) | count | detail |
|---|---|---|---|---|
| appointments:APT-006 | 2026-02-16 | 15 | 6 | 6 row(s) held; re-checked when the roster or reference data changes |
| appointments:APT-007 | 2026-02-16 | 15 | 3 | 3 row(s) held for a steward |
| appointments:APT-009 | 2026-02-16 | 15 | 3 | 3 row(s) held; re-checked when the roster or reference data changes |
| labs:LAB-008 | 2026-02-16 | 15 | 5 | 5 row(s) held for a steward |
| labs:LAB-010 | 2026-02-16 | 15 | 4 | 4 row(s) held for a steward |
| labs:LAB-011 | 2026-02-16 | 15 | 4 | 4 row(s) held; re-checked when the roster or reference data changes |
| pharmacy:RX-006 | 2026-02-16 | 15 | 5 | 5 row(s) held for a steward |
| pharmacy:RX-008 | 2026-02-16 | 15 | 3 | 3 row(s) held for a steward |
| pharmacy:RX-010 | 2026-02-16 | 15 | 2 | 2 row(s) held; re-checked when the roster or reference data changes |
| roster:ROS-008 | 2026-02-16 | 15 | 3 | 3 row(s) held for a steward |
| screenings:SCR-007 | 2026-02-16 | 15 | 6 | 6 row(s) held for a steward |
| screenings:SCR-009 | 2026-02-16 | 15 | 4 | 4 row(s) held; re-checked when the roster or reference data changes |
| screenings:SCR-010 | 2026-02-16 | 15 | 3 | 3 row(s) held; re-checked when the roster or reference data changes |
| appointments:APT-008 | 2026-02-23 | 8 | 2 | 2 row(s) held for a steward |
| screenings:SCR-006 | 2026-02-23 | 8 | 2 | 2 row(s) held for a steward |
| screenings:SCR-011 | 2026-02-23 | 8 | 2 | 2 row(s) held for a steward |
| labs:LAB-009 | 2026-03-02 | 1 | 2 | 2 row(s) held for a steward |
| pharmacy:pharmacy_claims:late | 2026-03-03 | 0 | 1 | expected by 2026-03-02, 1 day(s) late |

Resolved:

- appointments:APT-001:appointments_main_20260302.csv - re-delivered, accepted or waived

## Measures (network)

| measure | rate | numerator / denominator | previous run |
|---|---|---|---|
| DEP_FOLLOWUP | 70.0% | 285 / 407 | 70.0% |
| DEP_SCREEN | 74.2% | 2612 / 3520 | 74.2% |
| DM_A1C_CONTROLLED | 45.9% | 141 / 307 | 45.9% |
| DM_A1C_POOR | 37.1% | 114 / 307 | 37.1% |
| DM_A1C_TESTED | 88.9% | 273 / 307 | 88.9% |
| PDC_DIABETES | 63.2% | 204 / 323 | 63.2% |
| PDC_RAS | 62.9% | 434 / 690 | 62.9% |
| PDC_STATIN | 63.1% | 349 / 553 | 63.1% |
| SDOH_SCREEN | 40.0% | 1316 / 3289 | 40.0% |

## De-identification

Leak gate: passed (12 of 12 checks).

| check | result | detail |
|---|---|---|
| no real record number anywhere | pass | searched 301,620 strings |
| no real full name anywhere | pass | searched 301,620 strings |
| no real e-mail anywhere | pass | searched 301,620 strings |
| no real phone anywhere | pass | searched 301,620 strings |
| no real street address anywhere | pass | searched 301,620 strings |
| every demo patient differs from the real one | pass | 4,997 patients compared |
| dates moved by one consistent, non-zero shift | pass | birth dates checked against shifts |
| policy transforms every registered identifier path | pass | 17 paths covered |
| patients point at clinics, worklists at patients | pass | all references resolve |
| extract carries only allowed columns | pass | ok |
| no age above 89 and no birth year for 90+ | pass | ok |
| restricted ZIP3 prefixes replaced by 000 | pass | ok |

Quasi-identifier group sizes in the extract (reported, not a pass/fail):

| quasi-identifiers | groups | smallest | records in groups below 11 |
|---|---|---|---|
| birth_year, sex, zip3 | 781 | 1 | 3335 (67%) |
| age_band, sex, zip3 | 57 | 1 | 55 (1%) |
