select
    lab_result_id,
    patient_id,
    collected_date,
    analyte,
    value,
    unit,
    censor
from {{ source('published', 'lab_results') }}
