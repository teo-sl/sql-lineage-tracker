CREATE TABLE inline_subquery_report AS
SELECT
    cust_totals.customer_id,
    cust_totals.total_spent,
    region_stats.country,
    region_stats.avg_regional_spend,
    (cust_totals.total_spent - region_stats.avg_regional_spend) as diff_from_regional_avg
FROM (
    -- Subquery 1: Calculate total spent per customer directly in FROM
    SELECT 
        so.customer_id,
        SUM(oi.quantity * rp.unit_price) as total_spent
    FROM stg_orders so
    JOIN raw_order_items oi ON so.order_id = oi.order_id
    JOIN raw_products rp ON oi.product_id = rp.product_id
    WHERE so.order_status = 'COMPLETED'
    GROUP BY so.customer_id
) cust_totals
JOIN (
    -- Subquery 2: Calculate regional average directly in JOIN
    SELECT 
        sc.country,
        AVG(ios.order_revenue) as avg_regional_spend
    FROM stg_customers sc
    JOIN int_order_summary ios ON sc.customer_id = ios.customer_id
    GROUP BY sc.country
) region_stats 
  ON cust_totals.customer_id = region_stats.country -- (dummy join condition)
WHERE cust_totals.total_spent > 500;
