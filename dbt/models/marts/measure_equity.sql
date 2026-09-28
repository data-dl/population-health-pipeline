-- Every measure stratified by race and ethnicity, language, payer, sex and age band.
--
--   rate            the group's rate
--   relative_rate   the group's rate over the rate of everyone else in the same stratifier
--   share_of_denominator / share_of_numerator   the group's share of the eligible population and of
--                   those meeting the measure (a composition view: a group that is 30% of the
--                   eligible and 15% of those screened stands out)
--
-- Small cells are suppressed before anything leaves the warehouse: a group with fewer than
-- min_cell_size patients, or whose numerator or its complement is between 1 and min_cell_size - 1,
-- shows no values - not even its denominator; and when a stratifier has exactly one suppressed group,
-- the next smallest group is suppressed too, so the hidden one cannot be recovered by subtracting the
-- others from the network total (published in measure_summary).
{% set min_cell = var('min_cell_size') %}

with long as (
    select r.measure_id, r.numerator, s.stratifier, s.group_value
    from {{ ref('measure_patient_results') }} r
    join (
        unpivot (
            select patient_id, race_ethnicity, language_group, payer_type, sex, age_band
            from {{ ref('int_patient_facts') }}
        ) on race_ethnicity, language_group, payer_type, sex, age_band
        into name stratifier value group_value
    ) s using (patient_id)
),

counts as (
    select measure_id, stratifier, group_value,
           count(*) as denominator,
           count(*) filter (where numerator) as numerator
    from long
    group by all
),

with_totals as (
    select *,
           sum(denominator) over (partition by measure_id, stratifier) as total_denominator,
           sum(numerator) over (partition by measure_id, stratifier) as total_numerator
    from counts
),

primary_suppression as (
    select *,
           denominator < {{ min_cell }}
           or numerator between 1 and {{ min_cell }} - 1
           or (denominator - numerator) between 1 and {{ min_cell }} - 1 as primary_suppressed
    from with_totals
),

ranked as (
    select *,
           sum(case when primary_suppressed then 1 else 0 end) over (partition by measure_id, stratifier)
               as suppressed_in_stratifier,
           row_number() over (partition by measure_id, stratifier, primary_suppressed
                              order by denominator, group_value) as size_rank
    from primary_suppression
),

final as (
    select *,
           primary_suppressed
           or (suppressed_in_stratifier = 1 and not primary_suppressed and size_rank = 1) as suppressed
    from ranked
)

select
    measure_id,
    stratifier,
    group_value,
    case when not suppressed then denominator end as denominator,
    case when not suppressed then numerator end as numerator,
    case when not suppressed then round(numerator / denominator, 4) end as rate,
    case when not suppressed and total_denominator > denominator
              and (total_numerator - numerator) > 0 then
        round((numerator / denominator) / ((total_numerator - numerator) / (total_denominator - denominator)), 3)
    end as relative_rate,
    case when not suppressed then round(denominator / total_denominator, 4) end as share_of_denominator,
    case when not suppressed and total_numerator > 0 then round(numerator / total_numerator, 4) end
        as share_of_numerator,
    suppressed,
    case when primary_suppressed then 'small cell'
         when suppressed then 'complementary' end as suppression_reason
from final
order by measure_id, stratifier, group_value
