-- ============================================================
-- RAW LAYER: raw_orders
-- Source table — simulates order data loaded from an external system
-- ============================================================
CREATE TABLE IF NOT EXISTS raw_orders (
    order_id        SERIAL PRIMARY KEY,
    customer_id     INTEGER NOT NULL,
    order_date      DATE NOT NULL,
    status          VARCHAR(50) DEFAULT 'pending',
    shipping_address TEXT,
    created_at      TIMESTAMP DEFAULT NOW()
);
