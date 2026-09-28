# CR-004 - Reference laboratory file layout, version 2

| | |
|---|---|
| Feed | labs (source `reference_lab`) |
| Raised by | Clinical data steward, after the laboratory's notice of change |
| Effective | the 2026-03-02 extract |
| Status | Implemented - layout v2 registered in `config/contracts/labs.yaml` |

## What changes

| v1 column | v2 column | canonical | note |
|---|---|---|---|
| OrderID | accession_id | lab_result_id | new identifier scheme (ACC...); no overlap with v1 ids |
| MRN | patient_mrn | patient_id | |
| Collected | collection_date | collected_date | still ISO dates |
| LOINC | loinc_code | loinc_code | |
| TestName | test_description | test_name | upper case in v2 |
| Result | result_value | result | |
| - | result_units | units | new: v1 units were implied by the code |
| RefRange | reference_range | reference_range | |
| AbnFlag | abnormal_flag | abnormal_flag | |
| - | specimen_id | specimen_id | new |

Part of the laboratory's network now reports HbA1c under LOINC 59261-8 in IFCC mmol/mol.

## Decisions

1. **One canonical table.** Both layouts map to the same canonical columns; the raw text of each layout
   stays in its own raw table (`raw.labs__reference_lab__v1`, `__v2`), so nothing is lost and either can
   be re-staged.
2. **Units become explicit.** `units` comes from the file when present, else from the code's default
   unit (`config/reference/lab_tests.yaml`). IFCC results are converted to NGSP %
   (0.09148 x IFCC + 2.152, rounded half up to one decimal, in exact decimal arithmetic).
3. **Unknown units are held, never converted.** A unit the contract does not list for the analyte (for
   example `mg%`) holds the row under LAB-009 for the steward.
4. **Archive before the first v2 load.** The pipeline snapshots `clean.labs` to
   `archive.labs__before_reference_lab_v2` and records the change in `audit.schema_changes` the first
   time a v2 file loads.
5. **Old columns kept.** v1 rows keep a blank `specimen_id`; nothing is dropped from clean.

## Verification

- `tests/test_pipeline.py::test_layout_change_is_recognised_and_clean_archived_first`
- `tests/test_pipeline.py::test_ifcc_results_are_converted_and_censored_results_keep_their_bound`
- `tests/test_feeds_unit.py::test_lab_units_censoring_repairs_and_rejections`
- An unregistered layout (for example v1 plus a stray column) is still stopped with a shape report:
  `tests/test_feeds_unit.py::test_unregistered_layout_is_stopped_explained_and_can_be_waived`
