-- Every diabetic patient in the denominator is controlled (< 8.0), poorly controlled (> 9.0 or untested),
-- or in the band between - never both controlled and poorly controlled.
select c.patient_id
from {{ ref('measure_patient_results') }} c
join {{ ref('measure_patient_results') }} p on p.patient_id = c.patient_id
where c.measure_id = 'DM_A1C_CONTROLLED' and p.measure_id = 'DM_A1C_POOR' and c.numerator and p.numerator
