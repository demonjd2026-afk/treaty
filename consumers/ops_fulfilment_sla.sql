-- Operations: Fulfilment SLA report
select
    order_id,
    status,
    created_at,
    case
        when status = 'delivered' then 'done'
        when status = 'shipped'   then 'in transit'
        when status = 'paid'      then 'to pick'
        else 'other'
    end as fulfilment_stage
from analytics.orders
where created_at >= current_date - interval '30' day;
