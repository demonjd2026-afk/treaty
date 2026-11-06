-- Finance: Revenue Daily dashboard
select
    date_trunc('day', created_at)             as order_day,
    currency,
    count(distinct order_id)                  as orders,
    sum(amount)                               as gross_revenue,
    sum(case when status = 'cancelled' then amount else 0 end) as cancelled_revenue
from analytics.orders
where status in ('paid', 'shipped', 'delivered', 'cancelled')
group by 1, 2;
