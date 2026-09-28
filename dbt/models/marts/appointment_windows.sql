-- Kept-appointment rates per patient over the 30, 90 and 180 days up to the as-of date.
-- Kept rate = completed / (completed + no-show); cancellations and bookings do not count either way.
with appts as (
    select patient_id, appt_date, status
    from {{ ref('stg_appointments') }}
    where status in ('completed', 'no_show') and appt_date <= {{ as_of() }}
),

windows as (
    select
        patient_id,
        count(*) filter (where status = 'completed' and appt_date > {{ as_of() }} - 30) as kept_30,
        count(*) filter (where appt_date > {{ as_of() }} - 30) as due_30,
        count(*) filter (where status = 'completed' and appt_date > {{ as_of() }} - 90) as kept_90,
        count(*) filter (where appt_date > {{ as_of() }} - 90) as due_90,
        count(*) filter (where status = 'completed' and appt_date > {{ as_of() }} - 180) as kept_180,
        count(*) filter (where appt_date > {{ as_of() }} - 180) as due_180,
        count(*) filter (where status = 'no_show' and appt_date > {{ as_of() }} - 180) as no_shows_180,
        max(appt_date) filter (where status = 'completed') as last_completed_visit
    from appts
    group by patient_id
)

select
    p.patient_id,
    p.site_id,
    {{ as_of() }} as as_of_date,
    w.kept_30, w.due_30, case when w.due_30 > 0 then round(w.kept_30 / w.due_30, 3) end as kept_rate_30,
    w.kept_90, w.due_90, case when w.due_90 > 0 then round(w.kept_90 / w.due_90, 3) end as kept_rate_90,
    w.kept_180, w.due_180, case when w.due_180 > 0 then round(w.kept_180 / w.due_180, 3) end as kept_rate_180,
    coalesce(w.no_shows_180, 0) as no_shows_180,
    w.last_completed_visit,
    coalesce(w.no_shows_180 >= 3 or (w.due_180 >= 3 and w.kept_180 / w.due_180 < 0.7), false) as frequent_no_show
from {{ ref('stg_patients') }} p
left join windows w using (patient_id)
