-- Test: PIVOT and UNPIVOT operations

-- 1. Base table (monthly sales data)
CREATE TABLE monthly_sales AS
SELECT
    product_id,
    '2024-01-01' AS month,
    100 AS revenue
FROM raw_products
UNION ALL
SELECT
    product_id,
    '2024-02-01' AS month,
    150 AS revenue
FROM raw_products;


-- 2. PIVOT: Turn months into columns
CREATE TABLE sales_pivot AS
SELECT *
FROM monthly_sales
PIVOT (
    SUM(revenue)
    FOR month IN ('2024-01-01' AS jan_rev, '2024-02-01' AS feb_rev)
) AS p;


-- 3. UNPIVOT: Turn columns back into rows
CREATE TABLE sales_unpivot AS
SELECT
    product_id,
    month_name,
    revenue_amount
FROM sales_pivot
UNPIVOT (
    revenue_amount
    FOR month_name IN (jan_rev, feb_rev)
) AS u;
