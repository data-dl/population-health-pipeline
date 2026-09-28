-- One long table for every comparison: a row per scope x metric x (period, comparison period), with
-- the value in each. Any dashboard tile of the form "this week vs last week" or "year to date vs the
-- same days last year" reads the same shape, whatever the metric.
{% set periods = [
    ('last 7 days', 'previous 7 days', 7),
    ('last 28 days', 'previous 28 days', 28),
    ('last 90 days', 'previous 90 days', 90),
] %}

with appts as (
    select site_id, appt_date, status
    from {{ ref('stg_appointments') }}
    where status in ('completed', 'no_show')
),

scoped as (
    select 'network' as scope, 'ALL' as scope_id, appt_date, status from appts
    union all
    select 'clinic', site_id, appt_date, status from appts
),

comparisons as (
    {% for after, before, days in periods %}
    select scope, scope_id, '{{ after }}' as period_after, '{{ before }}' as period_before,
           count(*) filter (where status = 'completed' and appt_date > {{ as_of() }} - {{ days }}
                            and appt_date <= {{ as_of() }}) as kept_after,
           count(*) filter (where appt_date > {{ as_of() }} - {{ days }} and appt_date <= {{ as_of() }}) as due_after,
           count(*) filter (where status = 'completed' and appt_date > {{ as_of() }} - {{ 2 * days }}
                            and appt_date <= {{ as_of() }} - {{ days }}) as kept_before,
           count(*) filter (where appt_date > {{ as_of() }} - {{ 2 * days }}
                            and appt_date <= {{ as_of() }} - {{ days }}) as due_before
    from scoped group by scope, scope_id
    union all
    {% endfor %}
    select scope, scope_id, 'year to date', 'same days last year',
           count(*) filter (where status = 'completed' and appt_date between date_trunc('year', {{ as_of() }})
                            and {{ as_of() }}),
           count(*) filter (where appt_date between date_trunc('year', {{ as_of() }}) and {{ as_of() }}),
           count(*) filter (where status = 'completed' and appt_date between date_trunc('year', {{ as_of() }})
                            - interval 1 year and {{ as_of() }} - interval 1 year),
           count(*) filter (where appt_date between date_trunc('year', {{ as_of() }}) - interval 1 year
                            and {{ as_of() }} - interval 1 year)
    from scoped group by scope, scope_id
)

select
    scope,
    scope_id,
    'kept_rate' as metric,
    period_after,
    period_before,
    case when due_after > 0 then round(kept_after / due_after, 4) end as value_after,
    case when due_before > 0 then round(kept_before / due_before, 4) end as value_before,
    case when due_after > 0 and due_before > 0
         then round(kept_after / due_after - kept_before / due_before, 4) end as change,
    due_after as appointments_after,
    due_before as appointments_before
from comparisons
order by scope, scope_id, period_after
