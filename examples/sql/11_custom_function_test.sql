CREATE TABLE custom_function_report AS
SELECT 
    customer_id,
    finance_schema.calculate_tax(total_spent) as tax_amount,
    public.format_currency(diff_from_regional_avg, 'USD') as formatted_diff
FROM inline_subquery_report;
