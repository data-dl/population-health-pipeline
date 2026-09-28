select
    patient_id,
    site_id,
    birth_date,
    sex,
    race_ethnicity,
    language_group,
    payer_type,
    pcp_name,
    care_manager_name
from {{ source('published', 'patients') }}
