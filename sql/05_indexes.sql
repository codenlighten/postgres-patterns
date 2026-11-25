-- Study 5: indexes, and proving the planner uses them.
--
-- 200,000 orders; about 1% are 'open'; emails are stored in mixed case. The tests create the indexes one
-- at a time and read the query plan before and after, so the claim "this index is used" is checked, not
-- assumed.

create table orders (
    id          bigint generated always as identity primary key,
    customer_id int         not null,
    status      text        not null,
    total       numeric(10, 2) not null,
    email       text        not null,
    created_at  timestamptz not null
);

insert into orders (customer_id, status, total, email, created_at)
select
    (g * 7919) % 5000,
    case when g % 100 = 0 then 'open' when g % 3 = 0 then 'shipped' else 'closed' end,
    (g % 997) + 0.5,
    'User' || (g % 50000) || '@Example.com',
    timestamptz '2026-01-01' + (g * interval '10 seconds')
from generate_series(1, 200000) as g;

analyze orders;
