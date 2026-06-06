-- ============================================================
-- STAGING LAYER: stg_orders
-- Enriches raw_orders with extracted date parts
-- Derives: order_year, order_month from order_date
-- ============================================================
CREATE TABLE stg_orders AS
SELECT
    ro.order_id,
    ro.customer_id,
    ro.order_date,
    EXTRACT(YEAR FROM ro.order_date)::INTEGER   AS order_year,
    EXTRACT(MONTH FROM ro.order_date)::INTEGER  AS order_month,
    CASE
        WHEN ro.status = 'completed' THEN 'delivered'
        WHEN ro.status = 'cancelled' THEN 'cancelled'
        ELSE 'in_progress'
    END                                          AS order_status,
    ro.shipping_address
FROM raw_orders ro;
