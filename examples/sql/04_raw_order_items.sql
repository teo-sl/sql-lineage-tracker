-- ============================================================
-- RAW LAYER: raw_order_items
-- Source table — line items for each order
-- ============================================================
CREATE TABLE IF NOT EXISTS raw_order_items (
    item_id         SERIAL PRIMARY KEY,
    order_id        INTEGER NOT NULL,
    product_id      INTEGER NOT NULL,
    quantity        INTEGER NOT NULL DEFAULT 1,
    discount_pct    NUMERIC(5, 2) DEFAULT 0.00
);
