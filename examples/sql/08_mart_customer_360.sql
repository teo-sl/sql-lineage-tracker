-- ============================================================
-- MART LAYER: mart_customer_360
-- Final reporting table — Customer 360 view
-- Joins stg_customers + int_order_summary
-- Computes lifetime value, order count, average order value
-- ============================================================
CREATE TABLE mart_customer_360 AS
SELECT
    sc.customer_id,
    sc.full_name,
    sc.email,
    sc.country,
    sc.registered_at,
    COUNT(ios.order_id)                    AS total_orders,
    COALESCE(SUM(ios.order_revenue), 0)    AS lifetime_value,
    COALESCE(AVG(ios.order_revenue), 0)    AS avg_order_value,
    COALESCE(SUM(ios.total_quantity), 0)   AS total_items_purchased,
    MAX(ios.order_date)                    AS last_order_date,
    MIN(ios.order_date)                    AS first_order_date,
    CASE
        WHEN COUNT(ios.order_id) >= 10 THEN 'platinum'
        WHEN COUNT(ios.order_id) >= 5  THEN 'gold'
        WHEN COUNT(ios.order_id) >= 1  THEN 'silver'
        ELSE 'prospect'
    END                                    AS customer_tier
FROM stg_customers sc
LEFT JOIN int_order_summary ios ON sc.customer_id = ios.customer_id
GROUP BY
    sc.customer_id,
    sc.full_name,
    sc.email,
    sc.country,
    sc.registered_at;
