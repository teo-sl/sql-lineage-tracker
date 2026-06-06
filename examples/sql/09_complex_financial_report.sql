CREATE TABLE complex_financial_report AS
WITH monthly_revenue AS (
    SELECT 
        order_month,
        SUM(order_revenue) as gross_revenue,
        COUNT(DISTINCT order_id) as total_orders
    FROM int_order_summary
    WHERE order_status = 'COMPLETED'
    GROUP BY order_month
),
customer_LTV_segments AS (
    SELECT 
        customer_id,
        CASE 
            WHEN lifetime_value > 1000 THEN 'Platinum'
            WHEN lifetime_value > 500 THEN 'Gold'
            WHEN lifetime_value > 100 THEN 'Silver'
            ELSE 'Bronze'
        END AS loyalty_segment,
        total_items_purchased
    FROM mart_customer_360
),
segment_metrics AS (
    SELECT 
        loyalty_segment,
        COUNT(customer_id) as segment_size,
        AVG(total_items_purchased) as avg_items_per_customer
    FROM customer_LTV_segments
    GROUP BY loyalty_segment
),
regional_sales AS (
    SELECT 
        sc.country,
        so.order_month,
        SUM(oi.quantity * rp.unit_price) as regional_gross
    FROM stg_orders so
    JOIN stg_customers sc ON so.customer_id = sc.customer_id
    JOIN raw_order_items oi ON so.order_id = oi.order_id
    JOIN raw_products rp ON oi.product_id = rp.product_id
    WHERE so.order_status = 'COMPLETED'
    GROUP BY sc.country, so.order_month
)
SELECT 
    mr.order_month,
    mr.gross_revenue,
    sm.loyalty_segment,
    sm.segment_size,
    rs.country,
    rs.regional_gross,
    (rs.regional_gross / mr.gross_revenue) * 100 as regional_contribution_pct
FROM monthly_revenue mr
CROSS JOIN segment_metrics sm
LEFT JOIN regional_sales rs ON mr.order_month = rs.order_month
