create table test_set_ops as
select customer_id from raw_customers as t1
where t1.status = 'active'
intersect
select customer_id from raw_orders as t2
where t2.status = 'completed'
except
select customer_id from raw_customers as t3
where t3.email is null;
