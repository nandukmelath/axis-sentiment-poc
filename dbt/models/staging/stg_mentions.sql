select
    a.source_id,
    r.author,
    r.source,
    r.created_at,
    a.sentiment,
    a.score,
    a.intent,
    a.urgency,
    a.recommended_team,
    a.rbi_category,
    a.product,
    a.fraud_signal,
    a.churn_risk
from {{ source('axis', 'analysis') }} a
join {{ source('axis', 'raw_posts') }} r on a.source_id = r.source_id
