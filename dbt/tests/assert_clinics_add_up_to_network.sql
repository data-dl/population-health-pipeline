-- Clinic rows add up to the network row for every measure.
with clinics as (
    select measure_id, sum(denominator) as denominator, sum(numerator) as numerator
    from {{ ref('measure_summary') }}
    where site_id <> 'ALL'
    group by measure_id
)
select c.*, n.denominator as network_denominator, n.numerator as network_numerator
from clinics c
join {{ ref('measure_summary') }} n on n.measure_id = c.measure_id and n.site_id = 'ALL'
where c.denominator <> n.denominator or c.numerator <> n.numerator
