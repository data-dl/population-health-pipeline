select
    claim_id,
    patient_id,
    fill_date,
    drug_name,
    drug_class,
    days_supply
from {{ source('published', 'pharmacy_fills') }}
