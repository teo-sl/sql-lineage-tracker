-- ============================================================
-- RAW LAYER: raw_products
-- Source table — product catalog loaded from an external system
-- ============================================================
CREATE TABLE IF NOT EXISTS raw_products (
    product_id      SERIAL PRIMARY KEY,
    product_name    VARCHAR(255) NOT NULL,
    category        VARCHAR(100),
    unit_price      NUMERIC(12, 2) NOT NULL,
    created_at      TIMESTAMP DEFAULT NOW()
);
