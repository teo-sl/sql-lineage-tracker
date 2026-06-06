-- ============================================================
-- STAGING LAYER: stg_customers
-- Cleans and normalizes raw_customers
-- Derives: full_name (concatenation), normalized email (lowercase)
-- ============================================================
CREATE TABLE stg_customers AS
SELECT
    rc.customer_id,
    rc.first_name || ' ' || rc.last_name  AS full_name,
    LOWER(TRIM(rc.email))                 AS email,
    rc.phone,
    UPPER(rc.country)                     AS country,
    rc.created_at                         AS registered_at
FROM raw_customers rc;
