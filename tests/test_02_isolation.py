"""Study 2: what a transaction does and does not protect you from.

Each test drives two connections step by step from one thread, so the interleaving is exactly the one
described and the result does not depend on timing.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import psycopg
import psycopg.errors
import pytest
from psycopg import IsolationLevel

from patterns.db import Conn, connect, scalar
from patterns.retry import retry_transaction

# --- lost update -------------------------------------------------------------------------------------


def test_read_modify_write_loses_an_update_at_read_committed(make_db: Callable[..., str]) -> None:
    url = make_db("02_isolation.sql")
    a, b = connect(url), connect(url)  # READ COMMITTED, the default
    n_a = scalar(a, "select n from counters where id = 1")  # both read 0
    n_b = scalar(b, "select n from counters where id = 1")
    a.execute("update counters set n = %s where id = 1", (n_a + 1,))
    a.commit()
    b.execute("update counters set n = %s where id = 1", (n_b + 1,))  # overwrites A's result with a stale value
    b.commit()
    assert scalar(a, "select n from counters where id = 1") == 1  # two increments, one lost
    a.close()
    b.close()


def test_incrementing_inside_the_statement_loses_nothing(make_db: Callable[..., str]) -> None:
    url = make_db("02_isolation.sql")
    a, b = connect(url), connect(url)
    a.execute("update counters set n = n + 1 where id = 1")
    a.commit()
    b.execute("update counters set n = n + 1 where id = 1")  # the row's *current* value is used
    b.commit()
    assert scalar(a, "select n from counters where id = 1") == 2
    a.close()
    b.close()


def test_select_for_update_makes_the_second_session_wait_for_the_first(make_db: Callable[..., str]) -> None:
    url = make_db("02_isolation.sql")
    a = connect(url)
    b = connect(url, options="-c lock_timeout=200ms")  # so "waiting" shows up as an error, not a hang
    n_a = scalar(a, "select n from counters where id = 1 for update")
    a.execute("update counters set n = %s where id = 1", (n_a + 1,))
    with pytest.raises(psycopg.errors.LockNotAvailable):  # B is blocked while A holds the row
        b.execute("select n from counters where id = 1 for update")
    b.rollback()
    a.commit()
    n_b = scalar(b, "select n from counters where id = 1 for update")  # now B sees A's result
    assert n_b == 1
    b.execute("update counters set n = %s where id = 1", (n_b + 1,))
    b.commit()
    assert scalar(a, "select n from counters where id = 1") == 2
    a.close()
    b.close()


# --- write skew --------------------------------------------------------------------------------------


def _on_call(url: str) -> int:
    with connect(url, autocommit=True) as conn:
        return int(scalar(conn, "select count(*) from doctors where on_call"))


def _two_doctors_leave(url: str, isolation: IsolationLevel) -> tuple[int, int]:
    """Both doctors check that two are on call, then each takes themselves off. Returns (failures, left on call)."""
    a, b = connect(url, isolation=isolation), connect(url, isolation=isolation)
    for c in (a, b):
        assert scalar(c, "select count(*) from doctors where on_call") == 2  # each sees both on call
    failures = 0
    live: list[Conn] = []
    for conn, who in ((a, "alice"), (b, "bob")):
        try:
            conn.execute("update doctors set on_call = false where name = %s", (who,))
            live.append(conn)
        except psycopg.errors.SerializationFailure:  # PostgreSQL may refuse already at the statement
            conn.rollback()
            failures += 1
    for conn in live:
        try:
            conn.commit()
        except psycopg.errors.SerializationFailure:  # or at commit
            conn.rollback()
            failures += 1
    a.close()
    b.close()
    return failures, _on_call(url)


def test_write_skew_empties_the_on_call_list_at_repeatable_read(make_db: Callable[..., str]) -> None:
    failures, on_call = _two_doctors_leave(make_db("02_isolation.sql"), IsolationLevel.REPEATABLE_READ)
    assert failures == 0
    assert on_call == 0  # each transaction was consistent on its own snapshot; together they broke the rule


def test_serializable_aborts_one_of_the_two(make_db: Callable[..., str]) -> None:
    failures, on_call = _two_doctors_leave(make_db("02_isolation.sql"), IsolationLevel.SERIALIZABLE)
    assert failures >= 1
    assert on_call == 1  # the rule held


def test_retrying_the_aborted_transaction_makes_it_see_the_new_state_and_refuse(
    make_db: Callable[..., str],
) -> None:
    url = make_db("02_isolation.sql")
    barrier = threading.Barrier(2)
    results: list[str] = []

    def go_off_call(who: str) -> None:
        attempts = 0

        def once() -> str:
            nonlocal attempts
            attempts += 1
            with connect(url, isolation=IsolationLevel.SERIALIZABLE) as conn, conn.transaction():
                on_call = scalar(conn, "select count(*) from doctors where on_call")
                if attempts == 1:
                    barrier.wait(timeout=10)  # both read "2" before either writes
                if on_call < 2:
                    return "refused"  # the rule would break: do not leave
                conn.execute("update doctors set on_call = false where name = %s", (who,))
                return "left"

        results.append(retry_transaction(once))

    threads = [threading.Thread(target=go_off_call, args=(w,)) for w in ("alice", "bob")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert sorted(results) == ["left", "refused"]
    assert _on_call(url) == 1


def test_retry_gives_up_after_the_attempt_limit_and_rejects_nonsense() -> None:
    calls = 0

    def always_fails() -> None:
        nonlocal calls
        calls += 1
        raise psycopg.errors.SerializationFailure("again")

    with pytest.raises(psycopg.errors.SerializationFailure):
        retry_transaction(always_fails, attempts=3)
    assert calls == 3
    with pytest.raises(ValueError):
        retry_transaction(always_fails, attempts=0)


def test_other_errors_are_not_retried() -> None:
    calls = 0

    def broken() -> None:
        nonlocal calls
        calls += 1
        raise ZeroDivisionError

    with pytest.raises(ZeroDivisionError):
        retry_transaction(broken)
    assert calls == 1
