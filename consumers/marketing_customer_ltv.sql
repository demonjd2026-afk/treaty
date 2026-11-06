-- Growth marketing: Customer LTV model
select
    customer_id,
    min(created_at)  as first_order_at,
    count(*)         as lifetime_orders,
    sum(amount)      as lifetime_value,
    max(channel)     as last_known_channel
from analytics.orders
group by customer_id;
