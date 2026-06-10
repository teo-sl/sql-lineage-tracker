create table ta_test_union as
with ct1 as (
    select customer_id
    from raw_customers
    where phone='123'
),
with ct2 as (
    select customer_id
    from raw_customers
    where phone='1234'
) 
select * from (
    select customer_id, 'a1' as label 
    from ct1

) as st1
union all
select * from (
    select customer_id, 'a2' as label
    from ct2
) as st2;