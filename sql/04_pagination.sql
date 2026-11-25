-- Study 4: pagination while the data changes.
--
-- OFFSET pages by position, so a row inserted at the front shifts everything and the next page repeats
-- rows the reader already saw. Keyset pagination pages by "after the last row I saw", which is stable.
-- Ties need a tie-breaker: with equal timestamps, paging by timestamp alone silently skips rows.

create table events (
    id         bigint generated always as identity primary key,
    created_at timestamptz not null,
    payload    text not null
);

-- the keyset key, newest first: (created_at, id) both descending
create index events_feed on events (created_at desc, id desc);
