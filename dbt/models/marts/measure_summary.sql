-- Rates by clinic and for the whole network ('ALL'), with each measure's definition beside it.
select
    r.measure_id,
    d.measure_name,
    d.direction,
    coalesce(r.site_id, 'ALL') as site_id,
    count(*) as denominator,
    count(*) filter (where r.numerator) as numerator,
    round(count(*) filter (where r.numerator) / count(*), 4) as rate
from {{ ref('measure_patient_results') }} r
join {{ ref('measure_definitions') }} d using (measure_id)
group by grouping sets ((r.measure_id, d.measure_name, d.direction, r.site_id),
                        (r.measure_id, d.measure_name, d.direction))
order by r.measure_id, site_id
