-- Study 2: what "transaction" does and does not protect you from.
--
-- counters: a read-modify-write that two sessions can interleave (lost update).
-- doctors:  a rule over several rows ("someone must stay on call"). Two transactions that each check the
--           rule on their own snapshot and then change different rows can break it together (write skew).
--           REPEATABLE READ does not prevent that; SERIALIZABLE does, by aborting one of them. (Locking the
--           rows you read before deciding also works, but then every code path must remember to.)

create table counters (
    id int primary key,
    n  int not null
);
insert into counters values (1, 0);

create table doctors (
    name    text primary key,
    on_call boolean not null
);
insert into doctors values ('alice', true), ('bob', true);
