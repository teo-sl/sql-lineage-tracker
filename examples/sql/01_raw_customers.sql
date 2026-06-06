-- ============================================================
-- RAW LAYER: raw_customers
-- Source table — simulates data loaded from an external system
-- ============================================================
CREATE TABLE IF NOT EXISTS raw_customers (
    customer_id     SERIAL PRIMARY KEY,
    first_name      VARCHAR(100) NOT NULL,
    last_name       VARCHAR(100) NOT NULL,
    email           VARCHAR(255),
    phone           VARCHAR(50),
    country         VARCHAR(100),
    created_at      TIMESTAMP DEFAULT NOW()
);
