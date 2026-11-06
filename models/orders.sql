-- Producer model behind the orders contract (contracts/orders.yaml).
-- Change this file and the contract together, in the same branch.
create or replace view analytics.orders as
select
    o.id                         as order_id,
    o.customer_id                as customer_id,
    cast(o.total * o.fx_rate_to_usd as decimal(18,2)) as amount_usd,
    o.currency                   as currency,
    o.status                     as status,
    o.utm_channel                as channel,
    o.created_at                 as created_at
from raw.checkout_orders o
where o.is_test = false;
