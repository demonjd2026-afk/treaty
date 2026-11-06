-- Migration for consumer: marketing-ltv (Customer LTV model)
-- Contract: orders 1.4.0 -> 2.0.0
-- Cause: `amount` (order's own currency) removed, replaced by `amount_usd`
-- (converted to USD). Mechanical rename applied below: lifetime_value now
-- sums USD-normalized amounts instead of mixed-currency raw amounts. This
-- changes the metric's meaning for every customer with non-USD orders
-- (arguably more correct for cross-currency LTV comparison, but the actual
-- numbers will shift) -- marketing owner should confirm before sign-off.
--
-- Separate, unrelated finding from `treaty check`: this query reads
-- `channel`, which is not declared in consumers.yaml for this consumer
-- (lineage drift). `channel` itself is unchanged by this contract version
-- bump -- flagging for the steward/owner to add it to consumers.yaml's
-- `uses` list, not something this migration fixes.

select
    customer_id,
    min(created_at)  as first_order_at,
    count(*)         as lifetime_orders,
    sum(amount_usd)  as lifetime_value_usd,
    max(channel)     as last_known_channel
from analytics.orders
group by customer_id;
