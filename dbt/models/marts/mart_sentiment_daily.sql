select
    cast(created_at as date) as day,
    coalesce(rbi_category, 'not_applicable') as category,
    count(*) as mentions,
    round(avg(score), 3) as avg_score,
    round(sum(case when sentiment in ('negative', 'mixed') then 1 else 0 end) * 1.0 / count(*), 3) as neg_ratio
from {{ ref('stg_mentions') }}
group by 1, 2
