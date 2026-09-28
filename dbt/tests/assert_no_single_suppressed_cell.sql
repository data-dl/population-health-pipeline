-- Complementary suppression: a stratifier never hides exactly one group (it could be recovered by
-- subtracting the others from the total).
select measure_id, stratifier, count(*) filter (where suppressed) as suppressed_groups
from {{ ref('measure_equity') }}
group by measure_id, stratifier
having count(*) filter (where suppressed) = 1
