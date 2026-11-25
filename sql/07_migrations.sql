-- Study 7: changing a live table without stopping the application.
--
-- An ALTER TABLE needs an ACCESS EXCLUSIVE lock. If a long transaction is already reading the table, the
-- ALTER waits, and queries that arrive after it and need a conflicting lock on that table (which every
-- ordinary read or write does) queue behind the ALTER: one patient DDL statement can freeze a busy table.
-- `lock_timeout` bounds the wait: the ALTER gives up and you retry later. (Readers can still queue behind it
-- for up to that long, so keep the timeout short.)

create table accounts (
    id    bigint generated always as identity primary key,
    name  text not null,
    qty   int  not null
);

insert into accounts (name, qty) select 'account ' || g, g % 100 from generate_series(1, 100000) as g;
