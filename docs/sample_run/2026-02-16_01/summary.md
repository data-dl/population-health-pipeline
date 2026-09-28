# Run 2026-02-16_01 - as of 2026-02-16

**All clear** - 5 feeds checked - 6 file(s) loaded - measures refreshed - de-identified copy released

## Needs action

Nothing.

## Feeds

| feed | files | rows in | held | repaired | warned | gate | published |
|---|---|---|---|---|---|---|---|
| roster | roster_20260216.csv (published) | 4990 | 9 | 5 | 10 | PUBLISH | patient_attribution_history, patient_conditions, patients |
| screenings | screenings_20260216.csv (published) | 3049 | 25 | 0 | 8 | PUBLISH | screening_events, screening_items |
| labs | labs_20260216.csv (published) | 2248 | 12 | 30 | 0 | PUBLISH | lab_results |
| appointments | appointments_main_20260216.csv (published), appointments_mobile_20260216.csv (published) | 15641 | 24 | 0 | 5 | PUBLISH | appointments |
| pharmacy | rx_claims_20260216.csv (published) | 14040 | 20 | 0 | 0 | PUBLISH | pharmacy_claims, pharmacy_fills |

## Freshness

| feed | source | delivery | pipeline | newest delivered | newest published | days behind | detail |
|---|---|---|---|---|---|---|---|
| appointments | clinic_scheduling | ON_TIME | OK | 2026-02-15 | 2026-02-13 | 2 |  |
| appointments | mobile_unit | ON_TIME | OK | 2026-02-15 | 2026-02-13 | 2 |  |
| labs | reference_lab | ON_TIME | OK | 2026-02-13 | 2026-02-13 | 0 |  |
| pharmacy | pharmacy_claims | ON_TIME | OK | 2026-02-15 | 2026-02-15 | 0 |  |
| roster | ehr_roster | ON_TIME | OK | 2026-02-16 | 2026-02-16 | 0 |  |
| screenings | screening_platform | ON_TIME | OK | 2026-02-13 | 2026-02-13 | 0 |  |

## Rules that fired

| feed | rule | action | rows (new) | rows re-checked | what it checks |
|---|---|---|---|---|---|
| appointments | APT-006 | quarantine | 6 | 0 | Status code is mapped |
| appointments | APT-007 | quarantine | 3 | 0 | Clinic exists |
| appointments | APT-009 | quarantine | 15 | 0 | Patient is on the roster |
| appointments | APT-011 | warn | 5 | 0 | Visit type is mapped |
| labs | LAB-003 | repair | 30 | 0 | Medical record numbers keep their leading zeros |
| labs | LAB-008 | quarantine | 5 | 0 | Result is a number or a censored number |
| labs | LAB-010 | quarantine | 2 | 0 | Result is clinically plausible |
| labs | LAB-011 | quarantine | 5 | 0 | Patient is on the roster |
| pharmacy | RX-006 | quarantine | 5 | 0 | Days supply is a whole number from 1 to 365 |
| pharmacy | RX-008 | quarantine | 3 | 0 | Drug is in a tracked class |
| pharmacy | RX-010 | quarantine | 12 | 0 | Patient is on the roster |
| roster | ROS-005 | quarantine | 2 | 0 | Birth date is a real date |
| roster | ROS-007 | repair | 5 | 0 | Clinic codes are upper case |
| roster | ROS-008 | quarantine | 3 | 0 | Attributed clinic exists |
| roster | ROS-010 | quarantine | 4 | 0 | One row per record number |
| roster | ROS-011 | warn | 10 | 0 | Phone numbers that cannot be standardised are blanked |
| screenings | SCR-007 | quarantine | 6 | 0 | Item responses are whole numbers within the instrument's range |
| screenings | SCR-009 | quarantine | 4 | 0 | Administration mode is recognised |
| screenings | SCR-010 | quarantine | 15 | 0 | Patient is on the roster |
| screenings | SCR-012 | warn | 8 | 0 | Platform totals agree with the item responses |

## Issue register

15 opened, 0 resolved this run; 15 open.

| issue | since | age (days) | count | detail |
|---|---|---|---|---|
| appointments:APT-006 | 2026-02-16 | 0 | 6 | 6 row(s) held; re-checked when the roster or reference data changes |
| appointments:APT-007 | 2026-02-16 | 0 | 3 | 3 row(s) held for a steward |
| appointments:APT-009 | 2026-02-16 | 0 | 15 | 15 row(s) held; re-checked when the roster or reference data changes |
| labs:LAB-008 | 2026-02-16 | 0 | 5 | 5 row(s) held for a steward |
| labs:LAB-010 | 2026-02-16 | 0 | 2 | 2 row(s) held for a steward |
| labs:LAB-011 | 2026-02-16 | 0 | 5 | 5 row(s) held; re-checked when the roster or reference data changes |
| pharmacy:RX-006 | 2026-02-16 | 0 | 5 | 5 row(s) held for a steward |
| pharmacy:RX-008 | 2026-02-16 | 0 | 3 | 3 row(s) held for a steward |
| pharmacy:RX-010 | 2026-02-16 | 0 | 12 | 12 row(s) held; re-checked when the roster or reference data changes |
| roster:ROS-005 | 2026-02-16 | 0 | 2 | 2 row(s) held for a steward |
| roster:ROS-008 | 2026-02-16 | 0 | 3 | 3 row(s) held for a steward |
| roster:ROS-010 | 2026-02-16 | 0 | 4 | 4 row(s) held for a steward |
| screenings:SCR-007 | 2026-02-16 | 0 | 6 | 6 row(s) held for a steward |
| screenings:SCR-009 | 2026-02-16 | 0 | 4 | 4 row(s) held; re-checked when the roster or reference data changes |
| screenings:SCR-010 | 2026-02-16 | 0 | 15 | 15 row(s) held; re-checked when the roster or reference data changes |

## Measures (network)

| measure | rate | numerator / denominator | previous run |
|---|---|---|---|
| DEP_FOLLOWUP | 70.0% | 285 / 407 | - |
| DEP_SCREEN | 74.2% | 2612 / 3520 | - |
| DM_A1C_CONTROLLED | 45.9% | 141 / 307 | - |
| DM_A1C_POOR | 37.1% | 114 / 307 | - |
| DM_A1C_TESTED | 88.6% | 272 / 307 | - |
| PDC_DIABETES | 63.0% | 203 / 322 | - |
| PDC_RAS | 62.9% | 434 / 690 | - |
| PDC_STATIN | 62.6% | 346 / 553 | - |
| SDOH_SCREEN | 40.0% | 1316 / 3289 | - |

## De-identification

Leak gate: passed (12 of 12 checks).

| check | result | detail |
|---|---|---|
| no real record number anywhere | pass | searched 300,004 strings |
| no real full name anywhere | pass | searched 300,004 strings |
| no real e-mail anywhere | pass | searched 300,004 strings |
| no real phone anywhere | pass | searched 300,004 strings |
| no real street address anywhere | pass | searched 300,004 strings |
| every demo patient differs from the real one | pass | 4,981 patients compared |
| dates moved by one consistent, non-zero shift | pass | birth dates checked against shifts |
| policy transforms every registered identifier path | pass | 17 paths covered |
| patients point at clinics, worklists at patients | pass | all references resolve |
| extract carries only allowed columns | pass | ok |
| no age above 89 and no birth year for 90+ | pass | ok |
| restricted ZIP3 prefixes replaced by 000 | pass | ok |

Quasi-identifier group sizes in the extract (reported, not a pass/fail):

| quasi-identifiers | groups | smallest | records in groups below 11 |
|---|---|---|---|
| birth_year, sex, zip3 | 781 | 1 | 3334 (67%) |
| age_band, sex, zip3 | 57 | 1 | 55 (1%) |
