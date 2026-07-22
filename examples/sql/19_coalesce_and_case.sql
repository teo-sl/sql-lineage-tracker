create table report_product_pricing as
select 
    p.product_id,
    p.name as product_name,
    coalesce(p.price, 0) as original_price,
    case 
        when p.category = 'Electronics' then p.price * 0.9
        when p.category = 'Clothing' then p.price * 0.8
        else p.price
    end as discounted_price,
    case 
        when oi.quantity > 10 then 'Bulk'
        else 'Standard'
    end as pricing_tier
from raw_products p
left join raw_order_items oi on p.product_id = oi.product_id;
