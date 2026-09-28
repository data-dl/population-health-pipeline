# Run 2026-02-23_01 - as of 2026-02-23

**All clear** - 5 feeds checked - 6 file(s) loaded - measures refreshed - de-identified copy released

## Needs action

Nothing.

## Watch

- screenings: screenings_20260223.csv - SCR-002: file uses %m/%d/%Y (67 dates parsed); parsed consistently with it
- labs: 18 late row(s) for the closed measurement year; its results are restated
- pharmacy: 40 late row(s) for the closed measurement year; its results are restated

## Feeds

| feed | files | rows in | held | repaired | warned | gate | published |
|---|---|---|---|---|---|---|---|
| roster | roster_20260223.csv (published) | 5002 | 7 | 5 | 10 | PUBLISH | patient_attribution_history, patient_conditions, patients |
| screenings | screenings_20260223.csv (published) | 67 | 4 | 0 | 0 | PUBLISH | screening_events, screening_items |
| labs | labs_20260223.csv (published) | 55 | 2 | 0 | 18 | PUBLISH | lab_results |
| appointments | appointments_main_20260223.csv (published), appointments_mobile_20260223.csv (published) | 463 | 2 | 0 | 0 | PUBLISH | appointments |
| pharmacy | rx_claims_20260223.csv (published) | 290 | 0 | 0 | 40 | PUBLISH | pharmacy_claims, pharmacy_fills |

## Freshness

| feed | source | delivery | pipeline | newest delivered | newest published | days behind | detail |
|---|---|---|---|---|---|---|---|
| appointments | clinic_scheduling | ON_TIME | OK | 2026-02-22 | 2026-02-20 | 2 |  |
| appointments | mobile_unit | ON_TIME | OK | 2026-02-22 | 2026-02-20 | 2 |  |
| labs | reference_lab | ON_TIME | OK | 2026-02-20 | 2026-02-20 | 0 |  |
| pharmacy | pharmacy_claims | ON_TIME | OK | 2026-02-22 | 2026-02-22 | 0 |  |
| roster | ehr_roster | ON_TIME | OK | 2026-02-23 | 2026-02-23 | 0 |  |
| screenings | screening_platform | ON_TIME | OK | 2026-02-22 | 2026-02-20 | 2 |  |

## Rules that fired

| feed | rule | action | rows (new) | rows re-checked | what it checks |
|---|---|---|---|---|---|
| appointments | APT-006 | quarantine | 0 | 6 | Status code is mapped |
| appointments | APT-008 | quarantine | 2 | 0 | Completed or missed appointments are not in the future |
| appointments | APT-009 | quarantine | 0 | 3 | Patient is on the roster |
| labs | LAB-010 | quarantine | 2 | 0 | Result is clinically plausible |
| labs | LAB-011 | quarantine | 0 | 4 | Patient is on the roster |
| labs | LAB-013 | warn | 18 | 0 | Late rows for a closed measurement year are announced |
| pharmacy | RX-010 | quarantine | 0 | 2 | Patient is on the roster |
| pharmacy | RX-012 | warn | 40 | 0 | Late claims for a closed measurement year are announced |
| roster | ROS-007 | repair | 5 | 0 | Clinic codes are upper case |
| roster | ROS-008 | quarantine | 3 | 0 | Attributed clinic exists |
| roster | ROS-010 | quarantine | 4 | 0 | One row per record number |
| roster | ROS-011 | warn | 10 | 0 | Phone numbers that cannot be standardised are blanked |
| screenings | SCR-006 | quarantine | 2 | 0 | Screening date is not after the extract |
| screenings | SCR-009 | quarantine | 0 | 4 | Administration mode is recognised |
| screenings | SCR-010 | quarantine | 0 | 3 | Patient is on the roster |
| screenings | SCR-011 | quarantine | 2 | 0 | One version of each screening per file |
| screenings | SCR-002 | warn | file |  | Dates use the agreed format - file uses %m/%d/%Y (67 dates parsed); parsed consistently with it |

## Issue register

3 opened, 1 resolved this run; 17 open.

| issue | since | age (days) | count | detail |
|---|---|---|---|---|
| appointments:APT-006 | 2026-02-16 | 7 | 6 | 6 row(s) held; re-checked when the roster or reference data changes |
| appointments:APT-007 | 2026-02-16 | 7 | 3 | 3 row(s) held for a steward |
| appointments:APT-009 | 2026-02-16 | 7 | 3 | 3 row(s) held; re-checked when the roster or reference data changes |
| labs:LAB-008 | 2026-02-16 | 7 | 5 | 5 row(s) held for a steward |
| labs:LAB-010 | 2026-02-16 | 7 | 4 | 4 row(s) held for a steward |
| labs:LAB-011 | 2026-02-16 | 7 | 4 | 4 row(s) held; re-checked when the roster or reference data changes |
| pharmacy:RX-006 | 2026-02-16 | 7 | 5 | 5 row(s) held for a steward |
| pharmacy:RX-008 | 2026-02-16 | 7 | 3 | 3 row(s) held for a steward |
| pharmacy:RX-010 | 2026-02-16 | 7 | 2 | 2 row(s) held; re-checked when the roster or reference data changes |
| roster:ROS-008 | 2026-02-16 | 7 | 3 | 3 row(s) held for a steward |
| roster:ROS-010 | 2026-02-16 | 7 | 4 | 4 row(s) held for a steward |
| screenings:SCR-007 | 2026-02-16 | 7 | 6 | 6 row(s) held for a steward |
| screenings:SCR-009 | 2026-02-16 | 7 | 4 | 4 row(s) held; re-checked when the roster or reference data changes |
| screenings:SCR-010 | 2026-02-16 | 7 | 3 | 3 row(s) held; re-checked when the roster or reference data changes |
| appointments:APT-008 | 2026-02-23 | 0 | 2 | 2 row(s) held for a steward |
| screenings:SCR-006 | 2026-02-23 | 0 | 2 | 2 row(s) held for a steward |
| screenings:SCR-011 | 2026-02-23 | 0 | 2 | 2 row(s) held for a steward |

Resolved:

- roster:ROS-005 - no rows held any more (released or superseded)

## Measures (network)

| measure | rate | numerator / denominator | previous run |
|---|---|---|---|
| DEP_FOLLOWUP | 70.0% | 285 / 407 | 70.0% |
| DEP_SCREEN | 74.2% | 2612 / 3520 | 74.2% |
| DM_A1C_CONTROLLED | 45.9% | 141 / 307 | 45.9% |
| DM_A1C_POOR | 37.1% | 114 / 307 | 37.1% |
| DM_A1C_TESTED | 88.9% | 273 / 307 | 88.6% (restated) |
| PDC_DIABETES | 63.2% | 204 / 323 | 63.0% (restated) |
| PDC_RAS | 62.9% | 434 / 690 | 62.9% |
| PDC_STATIN | 63.1% | 349 / 553 | 62.6% (restated) |
| SDOH_SCREEN | 40.0% | 1316 / 3289 | 40.0% |

## De-identification

Leak gate: passed (12 of 12 checks).

| check | result | detail |
|---|---|---|
| no real record number anywhere | pass | searched 301,190 strings |
| no real full name anywhere | pass | searched 301,190 strings |
| no real e-mail anywhere | pass | searched 301,190 strings |
| no real phone anywhere | pass | searched 301,190 strings |
| no real street address anywhere | pass | searched 301,190 strings |
| every demo patient differs from the real one | pass | 4,995 patients compared |
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
