-- Proportion of days covered (PDC) per patient and drug class for the measurement year.
--
-- Eligible: two or more fills of the class on different days in the year. The treatment period runs
-- from the first fill (index date) to year end. An early refill of the same drug does not overlap
-- the previous supply: it starts when that supply ends. Different drugs in one class simply cover
-- the same days. Covered days are counted once, clipped to the treatment period.
--
-- The carry-forward is computed without recursion. With fills ordered i = 1..n, C_i the supply
-- dispensed before fill i, and f_i the fill day, the shifted start is
--     s_i = C_i + max over j <= i of (f_j - C_j)
-- which equals max(f_i, s_(i-1) + supply_(i-1)) unrolled - a running max, so two window passes.
with fills as (
    select patient_id, drug_class, drug_name, claim_id, fill_date, days_supply
    from {{ ref('stg_pharmacy_fills') }}
    where fill_date between {{ my_start() }} and {{ my_end() }}
),

eligible as (
    select patient_id, drug_class, min(fill_date) as index_date
    from fills
    group by patient_id, drug_class
    having count(distinct fill_date) >= 2
),

supplied_before as (
    select
        f.*,
        e.index_date,
        date_diff('day', date '2000-01-01', f.fill_date) as fill_day,
        cast(coalesce(sum(f.days_supply) over (
            partition by f.patient_id, f.drug_class, f.drug_name
            order by f.fill_date, f.claim_id
            rows between unbounded preceding and 1 preceding), 0) as bigint) as supply_before
    from fills f
    join eligible e using (patient_id, drug_class)
),

shifted as (
    select
        *,
        cast(supply_before + max(fill_day - supply_before) over (
            partition by patient_id, drug_class, drug_name
            order by fill_date, claim_id
            rows between unbounded preceding and current row) as bigint) as start_day
    from supplied_before
),

covered as (
    select distinct patient_id, drug_class, index_date, day
    from (
        select patient_id, drug_class, index_date,
               date '2000-01-01' + cast(unnest(range(start_day, start_day + cast(days_supply as bigint))) as integer)
                   as day
        from shifted
    )
    where day between index_date and {{ my_end() }}
)

select
    e.patient_id,
    e.drug_class,
    e.index_date,
    count(c.day) as days_covered,
    date_diff('day', e.index_date, {{ my_end() }}) + 1 as period_days,
    round(count(c.day) / (date_diff('day', e.index_date, {{ my_end() }}) + 1), 3) as pdc,
    -- 80% or more, in whole numbers so no rounding can move a patient across the line
    5 * count(c.day) >= 4 * (date_diff('day', e.index_date, {{ my_end() }}) + 1) as adherent
from eligible e
left join covered c using (patient_id, drug_class, index_date)
group by e.patient_id, e.drug_class, e.index_date
