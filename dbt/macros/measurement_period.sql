{#- The measurement year's first and last day, and the as-of date for rolling windows. -#}
{% macro my_start() %}make_date({{ var('measurement_year') }}, 1, 1){% endmacro %}
{% macro my_end() %}make_date({{ var('measurement_year') }}, 12, 31){% endmacro %}
{% macro as_of() %}cast('{{ var("as_of_date") }}' as date){% endmacro %}

{#- Whole years between two dates (an age), without the year-boundary counting of date_diff. -#}
{% macro age_on(birth_date, on_date) -%}
    (year({{ on_date }}) - year({{ birth_date }})
     - case when strftime({{ on_date }}, '%m-%d') < strftime({{ birth_date }}, '%m-%d') then 1 else 0 end)
{%- endmacro %}
