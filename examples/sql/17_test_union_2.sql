create table ta_test_union_2 as
with ct1 as (
    select customer_id
    from raw_customers
    where phone='123'
),
with ct2 as (
    select customer_id
    from (select * from raw_customers) as t1
    where phone='1234'
) 
select * from (
    select customer_id from raw_customers
    where phone='1234'
) as t1
union
select * from (
    select customer_id from raw_customers
    where phone='1235'
) as t2