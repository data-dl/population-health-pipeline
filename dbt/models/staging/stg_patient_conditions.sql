select distinct
    patient_id,
    condition_group
from {{ source('published', 'patient_conditions') }}
