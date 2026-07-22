create table mart_deep_nested_subquery as
select 
    t.customer_id,
    t.customer_name,
    t.order_count
from (
    select 
        c.customer_id,
        c.first_name || ' ' || c.last_name as customer_name,
        (
            select count(*) 
            from raw_orders o 
            where o.customer_id = c.customer_id
        ) as order_count
    from (
        select customer_id, first_name, last_name 
        from raw_customers
        where status = 'active'
    ) c
) t
where t.order_count > 0;
