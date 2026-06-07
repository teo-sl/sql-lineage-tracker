-- Test: Explicit PIVOT columns

CREATE TABLE explicit_pivot_test AS
SELECT
    product_id,
    jan_rev,
    feb_rev
FROM monthly_sales
PIVOT (
    SUM(revenue)
    FOR month IN ('2024-01-01' AS jan_rev, '2024-02-01' AS feb_rev)
) AS p;
