select
    screening_id,
    patient_id,
    screen_date,
    instrument,
    complete,
    total_score,
    positive,
    safety_flag,
    severity_band
from {{ source('published', 'screening_events') }}
