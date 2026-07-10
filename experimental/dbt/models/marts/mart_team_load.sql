select
    coalesce(recommended_team, 'none') as team,
    count(*) as items,
    sum(case when urgency in ('high', 'critical') then 1 else 0 end) as urgent,
    sum(fraud_signal) as fraud
from {{ ref('stg_mentions') }}
group by 1
