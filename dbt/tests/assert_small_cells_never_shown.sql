-- No stratified result may show a numerator, or a numerator complement, between 1 and min_cell_size - 1,
-- nor any value at all for a suppressed row.
select *
from {{ ref('measure_equity') }}
where (not suppressed and (numerator between 1 and {{ var('min_cell_size') }} - 1
                           or (denominator - numerator) between 1 and {{ var('min_cell_size') }} - 1
                           or denominator < {{ var('min_cell_size') }}))
   or (suppressed and (denominator is not null or numerator is not null or rate is not null
                       or relative_rate is not null or share_of_denominator is not null
                       or share_of_numerator is not null))
