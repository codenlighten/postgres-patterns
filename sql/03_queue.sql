-- Study 3: a job queue in a plain table.
--
-- `FOR UPDATE SKIP LOCKED` hands each worker rows that no other transaction has row-locked, so workers do
-- not wait for each other's rows and never receive the same job. It skips only row locks (a table-level
-- lock can still make a worker wait), and it is not strict FIFO: a job that is locked at the moment a
-- worker looks is passed over. The partial index keeps "find the next queued job" fast no matter how many
-- finished jobs pile up.

create table jobs (
    id         bigint generated always as identity primary key,
    payload    text not null,
    state      text not null default 'queued' check (state in ('queued', 'running', 'done')),
    claimed_by text,
    claimed_at timestamptz
);

create index jobs_queued on jobs (id) where state = 'queued';

insert into jobs (payload) select 'job ' || g from generate_series(1, 200) as g;
