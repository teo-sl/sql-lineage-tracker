-- ============================================================
-- INTERMEDIATE LAYER: int_order_summary
-- Joins stg_orders + raw_order_items + raw_products
-- Computes per-order revenue with discount applied
-- Uses CTE for clarity
-- ============================================================
CREATE TABLE int_order_summary AS
WITH line_items AS (
    SELECT
        oi.order_id,
        oi.product_id,
        oi.quantity,
        oi.discount_pct,
        p.product_name,
        p.category,
        p.unit_price,
        (oi.quantity * p.unit_price * (1 - oi.discount_pct / 100.0)) AS line_total
    FROM raw_order_items oi
    JOIN raw_products p ON oi.product_id = p.product_id
)
SELECT
    so.order_id,
    so.customer_id,
    so.order_date,
    so.order_year,
    so.order_month,
    so.order_status,
    COUNT(li.product_id)    AS num_items,
    SUM(li.line_total)      AS order_revenue,
    SUM(li.quantity)         AS total_quantity,
    MAX(li.category)         AS primary_category
FROM stg_orders so
JOIN line_items li ON so.order_id = li.order_id
GROUP BY
    so.order_id,
    so.customer_id,
    so.order_date,
    so.order_year,
    so.order_month,
    so.order_status;
