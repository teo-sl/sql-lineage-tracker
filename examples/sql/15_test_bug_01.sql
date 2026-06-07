create table test_bug_01 as
select t2.*
from raw_customers t2 inner join (
    select customer_id from raw_orders 
) as t3 on t2.customer_id = t3.customer_id