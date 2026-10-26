select
    id,
    amount,
    {{ var("quality_threshold") }} as quality_threshold
from {{ ref('regions') }}
where amount >= {{ var("quality_threshold") }}
