-- One row per patient per measure they are eligible for (the denominator), with whether they meet it
-- (the numerator) and the value that decided it. Every summary and every stratified table is built
-- from this, so a rate can always be traced to the patients behind it.
with facts as (
    select * from {{ ref('int_patient_facts') }}
),

screens as (
    select * from {{ ref('stg_screening_events') }} where complete
),

phq9_in_year as (
    select patient_id, count(*) as n
    from screens
    where instrument = 'PHQ9' and screen_date between {{ my_start() }} and {{ my_end() }}
    group by patient_id
),

sdoh_in_year as (
    select patient_id, count(*) as n
    from screens
    where instrument = 'SDOH5' and screen_date between {{ my_start() }} and {{ my_end() }}
    group by patient_id
),

first_positive as (
    select patient_id, min(screen_date) as index_date
    from screens
    where instrument = 'PHQ9' and total_score >= 10
      and screen_date between {{ my_start() }} and make_date({{ var('measurement_year') }}, 12, 1)
    group by patient_id
),

follow_up_contacts as (
    select patient_id, appt_date as contact_date from {{ ref('stg_appointments') }} where status = 'completed'
    union all
    select patient_id, screen_date from screens where instrument = 'PHQ9'
),

followed_up as (
    select p.patient_id, min(c.contact_date) as first_contact
    from first_positive p
    join follow_up_contacts c
      on c.patient_id = p.patient_id
     and c.contact_date > p.index_date
     and c.contact_date <= p.index_date + 30
    group by p.patient_id
),

a1c_ranked as (
    select patient_id, value, collected_date,
           row_number() over (partition by patient_id order by collected_date desc, lab_result_id desc) as rn
    from {{ ref('stg_lab_results') }}
    where analyte = 'HBA1C' and collected_date between {{ my_start() }} and {{ my_end() }}
),

latest_a1c as (
    select patient_id, value, collected_date from a1c_ranked where rn = 1
),

diabetes_denominator as (
    select f.patient_id, f.site_id, l.value as latest_value
    from facts f
    left join latest_a1c l using (patient_id)
    where f.has_diabetes and f.age_my_end between 18 and 75 and f.active_my
)

select 'DEP_SCREEN' as measure_id, f.patient_id, f.site_id, s.n is not null as numerator,
       cast(coalesce(s.n, 0) as varchar) || ' complete PHQ-9' as detail
from facts f left join phq9_in_year s using (patient_id)
where f.active_my and f.age_my_end >= 12

union all
select 'DEP_FOLLOWUP', f.patient_id, f.site_id, u.first_contact is not null,
       'positive ' || cast(p.index_date as varchar)
       || coalesce('; follow-up ' || cast(u.first_contact as varchar), '; no follow-up within 30 days')
from first_positive p
join facts f using (patient_id)
left join followed_up u using (patient_id)

union all
select 'DM_A1C_TESTED', patient_id, site_id, latest_value is not null,
       coalesce('latest ' || cast(latest_value as varchar) || '%', 'no HbA1c in the year')
from diabetes_denominator

union all
select 'DM_A1C_CONTROLLED', patient_id, site_id, coalesce(latest_value < 8.0, false),
       coalesce('latest ' || cast(latest_value as varchar) || '%', 'no HbA1c in the year')
from diabetes_denominator

union all
select 'DM_A1C_POOR', patient_id, site_id, latest_value is null or latest_value > 9.0,
       coalesce('latest ' || cast(latest_value as varchar) || '%', 'no HbA1c in the year')
from diabetes_denominator

union all
select case d.drug_class when 'diabetes_oral' then 'PDC_DIABETES' when 'ras_antagonist' then 'PDC_RAS'
            when 'statin' then 'PDC_STATIN' end,
       d.patient_id, f.site_id, d.adherent,
       'PDC ' || cast(d.pdc as varchar) || ' (' || cast(d.days_covered as varchar) || ' of '
       || cast(d.period_days as varchar) || ' days)'
from {{ ref('int_pdc') }} d
join facts f using (patient_id)

union all
select 'SDOH_SCREEN', f.patient_id, f.site_id, s.n is not null,
       cast(coalesce(s.n, 0) as varchar) || ' complete social-needs screen'
from facts f left join sdoh_in_year s using (patient_id)
where f.active_my and f.age_my_end >= 18
