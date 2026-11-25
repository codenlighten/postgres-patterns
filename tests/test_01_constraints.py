"""Study 1: the database refuses bad data, including data that is only bad in combination."""

from __future__ import annotations

import threading
from collections.abc import Callable

import psycopg
import psycopg.errors
import pytest

from patterns.db import connect, scalar

BOOK = "insert into bookings (room_id, during, guest) values (%s, tstzrange(%s, %s), %s)"
T10, T11, T12, T13, T14 = (f"2026-03-01 {h}:00+00" for h in (10, 11, 12, 13, 14))


def test_overlapping_bookings_of_one_room_are_refused(make_db: Callable[..., str]) -> None:
    with connect(make_db("01_constraints.sql"), autocommit=True) as c:
        c.execute(BOOK, (1, T10, T12, "ann"))
        with pytest.raises(psycopg.errors.ExclusionViolation):
            c.execute(BOOK, (1, T11, T13, "bob"))  # overlaps 11:00 to 12:00
        assert scalar(c, "select count(*) from bookings") == 1


def test_back_to_back_bookings_and_other_rooms_are_allowed(make_db: Callable[..., str]) -> None:
    with connect(make_db("01_constraints.sql"), autocommit=True) as c:
        c.execute(BOOK, (1, T10, T12, "ann"))
        c.execute(BOOK, (1, T12, T14, "bob"))  # starts exactly when the first ends: '[)' ranges do not overlap
        c.execute(BOOK, (2, T10, T12, "cy"))  # the same time in a different room
        assert scalar(c, "select count(*) from bookings") == 3


@pytest.mark.parametrize(
    ("args", "error"),
    [
        ((1, T10, T10, "ann"), psycopg.errors.CheckViolation),  # empty range
        ((1, T10, None, "ann"), psycopg.errors.CheckViolation),  # no end
        ((1, T10, "infinity", "ann"), psycopg.errors.CheckViolation),  # an end at the value infinity
        ((1, "-infinity", T12, "ann"), psycopg.errors.CheckViolation),  # a start at -infinity
        ((1, None, T12, "ann"), psycopg.errors.CheckViolation),  # no start
        ((99, T10, T12, "ann"), psycopg.errors.ForeignKeyViolation),  # no such room
        ((1, T10, T12, ""), psycopg.errors.CheckViolation),  # empty guest name
        ((1, T10, T12, "x" * 81), psycopg.errors.CheckViolation),  # guest name too long
        ((1, T12, T10, "ann"), psycopg.errors.DataError),  # a range cannot end before it starts
    ],
)
def test_each_kind_of_bad_row_is_refused_with_its_own_error(
    make_db: Callable[..., str], args: tuple[object, ...], error: type[Exception]
) -> None:
    with connect(make_db("01_constraints.sql"), autocommit=True) as c, pytest.raises(error):
        c.execute(BOOK, args)


def test_room_names_are_unique(make_db: Callable[..., str]) -> None:
    with (
        connect(make_db("01_constraints.sql"), autocommit=True) as c,
        pytest.raises(psycopg.errors.UniqueViolation),
    ):
        c.execute("insert into rooms (id, name) values (3, 'Blue')")


def _race(url: str, statement: str, attempts: int = 16, options: str | None = None) -> list[str]:
    """Starts ``attempts`` sessions at the same moment, each running ``statement`` for the same room and time.

    Returns one outcome per session. Any unexpected error in a worker thread is re-raised here, and a thread
    that is still running after the generous timeout (a hang guard, not a performance limit) fails the test.
    """
    barrier = threading.Barrier(attempts)
    outcomes: list[str] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def attempt(i: int) -> None:
        try:
            with connect(url, autocommit=True, options=options) as c:
                barrier.wait(timeout=30)  # start together, so the attempts really race
                try:
                    row = c.execute(statement, (1, T10, T12, f"guest {i}")).fetchone()
                    result = "booked" if row is not None else "refused"
                except psycopg.errors.ExclusionViolation:
                    result = "refused"
                except psycopg.errors.DeadlockDetected:
                    result = "deadlock"
            with lock:
                outcomes.append(result)
        except BaseException as err:  # noqa: BLE001 - reported to the main thread below
            with lock:
                errors.append(err)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(attempts)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert not any(t.is_alive() for t in threads), "a session is still running: hang guard tripped"
    if errors:
        raise errors[0]
    return outcomes


def test_a_plain_insert_race_never_double_books_but_losers_may_be_killed_by_deadlock(
    make_db: Callable[..., str],
) -> None:
    """The constraint always holds. But sessions inserting the same slot wait on each other's uncommitted rows,
    and PostgreSQL resolves the resulting wait cycles by aborting some of them with "deadlock detected", after
    waiting `deadlock_timeout` (1 s by default). Whether and how many deadlock varies from run to run."""
    url = make_db("01_constraints.sql")
    # A short deadlock_timeout makes the demonstration quick; the default is 1 s.
    outcomes = _race(url, BOOK + " returning id", options="-c deadlock_timeout=100ms")
    assert len(outcomes) == 16
    assert outcomes.count("booked") == 1
    assert set(outcomes) <= {"booked", "refused", "deadlock"}
    with connect(url, autocommit=True) as c:
        assert scalar(c, "select count(*) from bookings") == 1


def test_on_conflict_do_nothing_gives_one_winner_and_clean_answers_in_the_same_race(
    make_db: Callable[..., str],
) -> None:
    """`INSERT ... ON CONFLICT DO NOTHING` reports a conflict as "no row returned" instead of raising. In this
    single-slot race every loser got that clean answer and none was a deadlock victim, in every run I made
    (the README says how many). That is an observation about this race, not a general guarantee."""
    url = make_db("01_constraints.sql")
    outcomes = _race(url, BOOK + " on conflict do nothing returning id")
    assert sorted(outcomes) == ["booked"] + ["refused"] * 15
    with connect(url, autocommit=True) as c:
        assert scalar(c, "select count(*) from bookings") == 1
