-- Study 1: let the database refuse bad data, including data that is only bad in combination.
--
-- A CHECK tests one row. UNIQUE and foreign keys enforce relationships between rows. An EXCLUSION
-- constraint generalises "no two rows may conflict" from equality to any operator: here, two bookings of
-- the same room whose time ranges overlap. It is enforced by the database itself, so no race between two
-- application servers can slip past it.

create extension if not exists btree_gist;   -- lets `room_id with =` share a GiST index with a range

create table rooms (
    id   int  primary key,
    name text not null unique
);

create table bookings (
    id      bigint generated always as identity primary key,
    room_id int    not null references rooms (id),
    during  tstzrange not null,
    guest   text   not null check (length(guest) between 1 and 80),
    -- a booking has two real ends: not empty, not unbounded, and no 'infinity' timestamps either
    -- (an unbounded range and a range ending at the value 'infinity' are different things)
    constraint real_range check (
        not isempty(during) and not lower_inf(during) and not upper_inf(during)
        and isfinite(lower(during)) and isfinite(upper(during))
    ),
    -- the same room cannot be booked for overlapping times; '[)' ranges let one end where the next begins
    constraint no_double_booking exclude using gist (room_id with =, during with &&)
);

insert into rooms (id, name) values (1, 'Blue'), (2, 'Green');
