-- Test: GROUP BY aggregations + window functions

CREATE TABLE window_groupby_report AS
WITH

-- Step 1: basic aggregation with GROUP BY
monthly_totals AS (
    SELECT
        sc.country,
        DATE_TRUNC('month', so.order_date)  AS order_month,
        SUM(oi.quantity * rp.unit_price)    AS gross_revenue,
        COUNT(DISTINCT so.order_id)         AS order_count,
        AVG(oi.quantity)                    AS avg_qty
    FROM stg_orders so
    JOIN raw_order_items oi ON so.order_id = oi.order_id
    JOIN raw_products     rp ON oi.product_id = rp.product_id
    JOIN stg_customers    sc ON so.customer_id = sc.customer_id
    WHERE so.order_status = 'COMPLETED'
    GROUP BY sc.country, DATE_TRUNC('month', so.order_date)
),

-- Step 2: window functions over the aggregated data
ranked_months AS (
    SELECT
        country,
        order_month,
        gross_revenue,
        order_count,
        avg_qty,
        -- Running total partitioned by country
        SUM(gross_revenue) OVER (
            PARTITION BY country
            ORDER BY order_month
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        )                                           AS running_revenue,
        -- Rank within country by revenue
        RANK() OVER (
            PARTITION BY country
            ORDER BY gross_revenue DESC
        )                                           AS revenue_rank,
        -- Month-over-month change
        LAG(gross_revenue, 1, 0) OVER (
            PARTITION BY country
            ORDER BY order_month
        )                                           AS prev_month_revenue,
        -- Moving average over last 3 months
        AVG(gross_revenue) OVER (
            PARTITION BY country
            ORDER BY order_month
            ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
        )                                           AS rolling_3m_avg
    FROM monthly_totals
),

-- Step 3: filter top-ranked months + compute derived metrics
top_months AS (
    SELECT
        country,
        order_month,
        gross_revenue,
        running_revenue,
        revenue_rank,
        prev_month_revenue,
        rolling_3m_avg,
        -- MoM growth rate
        CASE
            WHEN prev_month_revenue = 0 THEN NULL
            ELSE ROUND((gross_revenue - prev_month_revenue) / prev_month_revenue * 100, 2)
        END                                         AS mom_growth_pct
    FROM ranked_months
    WHERE revenue_rank <= 10
)

SELECT
    t.country,
    t.order_month,
    t.gross_revenue,
    t.running_revenue,
    t.revenue_rank,
    t.rolling_3m_avg,
    t.mom_growth_pct,
    -- Dense rank across ALL countries combined
    DENSE_RANK() OVER (ORDER BY t.gross_revenue DESC) AS global_rank
FROM top_months t
ORDER BY t.country, t.order_month;
