create table mart_customer_loyalty as
with customer_orders as (
    select 
        c.customer_id,
        c.first_name,
        c.last_name,
        o.order_id,
        o.order_date,
        o.status
    from raw_customers c
    left join raw_orders o on c.customer_id = o.customer_id
),
aggregated_orders as (
    select 
        customer_id,
        count(distinct order_id) as total_orders,
        max(order_date) as last_order_date
    from customer_orders
    where status = 'completed'
    group by customer_id
)
select 
    c.customer_id,
    c.first_name || ' ' || c.last_name as full_name,
    coalesce(a.total_orders, 0) as loyalty_points,
    a.last_order_date
from raw_customers c
left join aggregated_orders a on c.customer_id = a.customer_id;
