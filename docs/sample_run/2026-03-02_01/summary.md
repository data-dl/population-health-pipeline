# Run 2026-03-02_01 - as of 2026-03-02

**1 item(s) need action** - 5 feeds checked - 4 file(s) loaded - 1 stopped - measures refreshed - de-identified copy released

## Needs action

- **appointments**: publication held - appointments_main_20260302.csv is blocked by APT-001 (135 rows against a usual 418 (0.32x)); the feed does not publish past a gap until it is re-delivered, accepted or waived

## Watch

- pharmacy (pharmacy_claims): delivery expected today, not yet arrived
- labs: layout v1 -> v2 recognised (CR-004); clean table archived before loading
- labs: 8 late row(s) for the closed measurement year; its results are restated

## Feeds

| feed | files | rows in | held | repaired | warned | gate | published |
|---|---|---|---|---|---|---|---|
| roster | roster_20260302.csv (published) | 5000 | 3 | 5 | 10 | PUBLISH | patient_attribution_history, patient_conditions, patients |
| screenings | screenings_20260302.csv (published) | 49 | 0 | 0 | 0 | PUBLISH | screening_events, screening_items |
| labs | labs_20260302.csv (published) | 38 | 2 | 0 | 8 | PUBLISH | lab_results |
| appointments | appointments_main_20260302.csv (blocked), appointments_mobile_20260302.csv (merged) | 166 | 0 | 0 | 0 | HOLD | held |
| pharmacy | nothing new | 0 | 0 | 0 | 0 | PUBLISH | unchanged |

## Freshness

| feed | source | delivery | pipeline | newest delivered | newest published | days behind | detail |
|---|---|---|---|---|---|---|---|
| appointments | clinic_scheduling | ON_TIME | STUCK | 2026-02-25 | 2026-02-20 | 5 | appointments_main_20260302.csv blocked by APT-001 |
| appointments | mobile_unit | ON_TIME | STUCK | 2026-03-01 | 2026-02-20 | 9 | appointments_mobile_20260302.csv waiting at merged |
| labs | reference_lab | ON_TIME | OK | 2026-02-27 | 2026-02-27 | 0 |  |
| pharmacy | pharmacy_claims | DUE | OK | 2026-02-22 | 2026-02-22 | 0 | expected today (2026-03-02), not yet arrived |
| roster | ehr_roster | ON_TIME | OK | 2026-03-02 | 2026-03-02 | 0 |  |
| screenings | screening_platform | ON_TIME | OK | 2026-02-27 | 2026-02-27 | 0 |  |

## Rules that fired

| feed | rule | action | rows (new) | rows re-checked | what it checks |
|---|---|---|---|---|---|
| appointments | APT-006 | quarantine | 0 | 6 | Status code is mapped |
| appointments | APT-009 | quarantine | 0 | 3 | Patient is on the roster |
| labs | LAB-009 | quarantine | 2 | 0 | Unit is valid for the analyte |
| labs | LAB-011 | quarantine | 0 | 4 | Patient is on the roster |
| labs | LAB-013 | warn | 8 | 0 | Late rows for a closed measurement year are announced |
| pharmacy | RX-010 | quarantine | 0 | 2 | Patient is on the roster |
| roster | ROS-007 | repair | 5 | 0 | Clinic codes are upper case |
| roster | ROS-008 | quarantine | 3 | 0 | Attributed clinic exists |
| roster | ROS-011 | warn | 10 | 0 | Phone numbers that cannot be standardised are blanked |
| screenings | SCR-009 | quarantine | 0 | 4 | Administration mode is recognised |
| screenings | SCR-010 | quarantine | 0 | 3 | Patient is on the roster |
| appointments | APT-001 | blocked | file |  | Delivery volume is in the usual range - 135 rows against a usual 418 (0.32x) |

## Issue register

2 opened, 1 resolved this run; 18 open.

| issue | since | age (days) | count | detail |
|---|---|---|---|---|
| appointments:APT-006 | 2026-02-16 | 14 | 6 | 6 row(s) held; re-checked when the roster or reference data changes |
| appointments:APT-007 | 2026-02-16 | 14 | 3 | 3 row(s) held for a steward |
| appointments:APT-009 | 2026-02-16 | 14 | 3 | 3 row(s) held; re-checked when the roster or reference data changes |
| labs:LAB-008 | 2026-02-16 | 14 | 5 | 5 row(s) held for a steward |
| labs:LAB-010 | 2026-02-16 | 14 | 4 | 4 row(s) held for a steward |
| labs:LAB-011 | 2026-02-16 | 14 | 4 | 4 row(s) held; re-checked when the roster or reference data changes |
| pharmacy:RX-006 | 2026-02-16 | 14 | 5 | 5 row(s) held for a steward |
| pharmacy:RX-008 | 2026-02-16 | 14 | 3 | 3 row(s) held for a steward |
| pharmacy:RX-010 | 2026-02-16 | 14 | 2 | 2 row(s) held; re-checked when the roster or reference data changes |
| roster:ROS-008 | 2026-02-16 | 14 | 3 | 3 row(s) held for a steward |
| screenings:SCR-007 | 2026-02-16 | 14 | 6 | 6 row(s) held for a steward |
| screenings:SCR-009 | 2026-02-16 | 14 | 4 | 4 row(s) held; re-checked when the roster or reference data changes |
| screenings:SCR-010 | 2026-02-16 | 14 | 3 | 3 row(s) held; re-checked when the roster or reference data changes |
| appointments:APT-008 | 2026-02-23 | 7 | 2 | 2 row(s) held for a steward |
| screenings:SCR-006 | 2026-02-23 | 7 | 2 | 2 row(s) held for a steward |
| screenings:SCR-011 | 2026-02-23 | 7 | 2 | 2 row(s) held for a steward |
| appointments:APT-001:appointments_main_20260302.csv | 2026-03-02 | 0 | 1 | 135 rows against a usual 418 (0.32x) |
| labs:LAB-009 | 2026-03-02 | 0 | 2 | 2 row(s) held for a steward |

Resolved:

- roster:ROS-010 - no rows held any more (released or superseded)

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
| no real record number anywhere | pass | searched 301,580 strings |
| no real full name anywhere | pass | searched 301,580 strings |
| no real e-mail anywhere | pass | searched 301,580 strings |
| no real phone anywhere | pass | searched 301,580 strings |
| no real street address anywhere | pass | searched 301,580 strings |
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

## Messages sent

- `#data-ops-status` labs: layout v1 -> v2 (CR-004)
- `#data-ops-alerts` appointments: appointments_main_20260302.csv stopped by APT-001 (delivery volume is in the usual range)
- `#data-ops-alerts` appointments: publication held
