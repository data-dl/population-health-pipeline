select
    source,
    appointment_id,
    patient_id,
    site_id,
    appt_date,
    visit_type,
    status
from {{ source('published', 'appointments') }}
