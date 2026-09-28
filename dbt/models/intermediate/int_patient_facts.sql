-- One row per patient: attribution, the strata equity reporting uses, age at year end, whether the
-- patient was seen in the measurement year, and condition flags.
with visits as (
    select
        patient_id,
        count(*) filter (where status = 'completed' and appt_date between {{ my_start() }} and {{ my_end() }})
            as completed_visits_my
    from {{ ref('stg_appointments') }}
    group by patient_id
),

conditions as (
    select
        patient_id,
        bool_or(condition_group = 'diabetes_t2') as has_diabetes,
        bool_or(condition_group = 'hypertension') as has_hypertension,
        bool_or(condition_group = 'hyperlipidemia') as has_hyperlipidemia,
        bool_or(condition_group = 'depression') as has_depression
    from {{ ref('stg_patient_conditions') }}
    group by patient_id
)

select
    p.patient_id,
    p.site_id,
    p.care_manager_name,
    p.sex,
    p.race_ethnicity,
    p.language_group,
    p.payer_type,
    {{ age_on('p.birth_date', my_end()) }} as age_my_end,
    case
        when {{ age_on('p.birth_date', my_end()) }} < 18 then '0-17'
        when {{ age_on('p.birth_date', my_end()) }} < 40 then '18-39'
        when {{ age_on('p.birth_date', my_end()) }} < 65 then '40-64'
        else '65+'
    end as age_band,
    coalesce(v.completed_visits_my, 0) as completed_visits_my,
    coalesce(v.completed_visits_my, 0) > 0 as active_my,
    coalesce(c.has_diabetes, false) as has_diabetes,
    coalesce(c.has_hypertension, false) as has_hypertension,
    coalesce(c.has_hyperlipidemia, false) as has_hyperlipidemia,
    coalesce(c.has_depression, false) as has_depression
from {{ ref('stg_patients') }} p
left join visits v using (patient_id)
left join conditions c using (patient_id)
