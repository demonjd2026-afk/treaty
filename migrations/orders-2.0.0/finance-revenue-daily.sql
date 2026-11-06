-- Migration for consumer: finance-revenue-daily (Revenue Daily dashboard)
-- Contract: orders 1.4.0 -> 2.0.0
-- Cause: `amount` (decimal(12,2), order's own currency) was removed and
-- replaced by `amount_usd` (decimal(18,2), converted to USD). This is NOT
-- a safe mechanical rename: the dashboard groups by `currency` and reports
-- per-currency gross/cancelled revenue in that currency. Swapping in
-- amount_usd changes the figures themselves (USD-converted) while still
-- bucketing by the original currency code, which would silently mislabel
-- USD totals as local-currency revenue for every non-USD order.
--
-- Fix applied below: rename the column reference and the output aliases so
-- the semantic change is explicit (gross_revenue_usd / cancelled_revenue_usd)
-- rather than hidden behind the old names. Finance owner must confirm
-- whether per-currency breakdown is still wanted now that all amounts are
-- USD-normalized (the `currency` column becomes descriptive only, no longer
-- the unit of the summed figures).

select
    date_trunc('day', created_at)             as order_day,
    currency,
    count(distinct order_id)                  as orders,
    sum(amount_usd)                           as gross_revenue_usd,
    sum(case when status = 'cancelled' then amount_usd else 0 end) as cancelled_revenue_usd
from analytics.orders
where status in ('paid', 'shipped', 'delivered', 'cancelled')
group by 1, 2;
