-- Open care gaps per patient: the care manager's worklist. A gap is a measure the patient is eligible
-- for and does not meet (for the inverse HbA1c measure, one they do meet), plus operational flags.
with results as (
    select * from {{ ref('measure_patient_results') }}
),

gaps as (
    select patient_id, site_id,
           case measure_id
               when 'DM_A1C_TESTED' then 'hba1c_not_tested'
               when 'DM_A1C_POOR' then 'hba1c_poor_control'
               when 'DEP_SCREEN' then 'depression_screen_due'
               when 'DEP_FOLLOWUP' then 'depression_follow_up_missed'
               when 'SDOH_SCREEN' then 'social_needs_screen_due'
               when 'PDC_DIABETES' then 'adherence_diabetes_below_80'
               when 'PDC_RAS' then 'adherence_ras_below_80'
               when 'PDC_STATIN' then 'adherence_statin_below_80'
           end as gap,
           detail
    from results
    where (measure_id = 'DM_A1C_POOR' and numerator)
       or (measure_id in ('DM_A1C_TESTED', 'DEP_SCREEN', 'DEP_FOLLOWUP', 'SDOH_SCREEN', 'PDC_DIABETES', 'PDC_RAS',
                          'PDC_STATIN') and not numerator)
    union all
    select patient_id, site_id, 'frequent_no_show',
           cast(no_shows_180 as varchar) || ' no-shows in 180 days'
    from {{ ref('appointment_windows') }}
    where frequent_no_show
    union all
    select s.patient_id, p.site_id, 'phq9_item9_review',
           'self-harm item answered above 0 on ' || cast(max(s.screen_date) as varchar)
    from {{ ref('stg_screening_events') }} s
    join {{ ref('stg_patients') }} p using (patient_id)
    where s.safety_flag
    group by s.patient_id, p.site_id
)

select g.patient_id, g.site_id, p.care_manager_name, g.gap, g.detail
from gaps g
join {{ ref('stg_patients') }} p using (patient_id)
order by g.site_id, p.care_manager_name, g.patient_id, g.gap
