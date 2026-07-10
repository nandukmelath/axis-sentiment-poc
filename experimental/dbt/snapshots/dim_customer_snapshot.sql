{% snapshot dim_customer_snapshot %}
{{
    config(
        target_schema='snapshots',
        unique_key='customer_key',
        strategy='check',
        check_cols=['segment', 'rm_id', 'products_held', 'risk_flag', 'clv']
    )
}}
-- dbt-native SCD Type 2: tracks changes to the customer master over time
-- (the prod replacement for the hand-rolled SCD2 in warehouse/build.py).
select customer_key, customer_name, segment, rm_id, city, clv, risk_flag, products_held
from {{ source('axis', 'dim_customer') }}
{% endsnapshot %}
