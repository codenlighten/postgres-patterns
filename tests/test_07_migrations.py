"""Study 7: changing a live table without stopping the application."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import psycopg
import psycopg.errors
import pytest

from patterns.db import connect, scalar

ALTER = "alter table accounts add column note text"


def _wait_until_blocked(url: str, text: str, timeout: float = 10.0) -> None:
    """Waits until some session is stuck waiting for a lock while running a statement containing ``text``."""
    deadline = time.monotonic() + timeout
    with connect(url, autocommit=True) as watcher:
        while time.monotonic() < deadline:
            if scalar(
                watcher,
                "select count(*) from pg_stat_activity where wait_event_type = 'Lock' and query ilike %s",
                (f"%{text}%",),
            ):
                return
            time.sleep(0.02)
    raise AssertionError(f"no session became blocked running {text!r}")


def test_adding_a_column_with_a_default_does_not_rewrite_the_table_but_changing_a_type_does(
    make_db: Callable[..., str],
) -> None:
    with connect(make_db("07_migrations.sql"), autocommit=True) as c:
        original = scalar(c, "select pg_relation_filenode('accounts')")
        c.execute("alter table accounts add column status text not null default 'new'")
        assert scalar(c, "select pg_relation_filenode('accounts')") == original  # metadata only: no table rewrite
        assert scalar(c, "select status from accounts where id = 1") == "new"  # existing rows read the default

        c.execute("alter table accounts alter column qty type bigint")
        assert scalar(c, "select pg_relation_filenode('accounts')") != original  # the whole table was rewritten


def test_a_waiting_alter_table_blocks_every_new_reader_behind_it(make_db: Callable[..., str]) -> None:
    url = make_db("07_migrations.sql")
    long_reader = connect(url)
    long_reader.execute("select count(*) from accounts")  # an open transaction holding a lock on the table

    def migrate() -> None:
        with connect(url, autocommit=True) as c:
            c.execute(ALTER)  # needs ACCESS EXCLUSIVE, so it waits for the reader

    ddl = threading.Thread(target=migrate)
    ddl.start()
    _wait_until_blocked(url, "add column note")

    newcomer_conn = connect(url, autocommit=True, options="-c statement_timeout=400ms")
    with newcomer_conn as newcomer, pytest.raises(psycopg.errors.QueryCanceled):
        # an ordinary read, stuck behind the queued ALTER
        newcomer.execute("select 1 from accounts limit 1")

    long_reader.commit()  # the reader finishes, so the ALTER can proceed
    ddl.join(timeout=15)
    assert not ddl.is_alive()
    long_reader.close()


def test_lock_timeout_makes_the_migration_give_up_instead_of_freezing_the_table(
    make_db: Callable[..., str],
) -> None:
    url = make_db("07_migrations.sql")
    long_reader = connect(url)
    long_reader.execute("select count(*) from accounts")

    impatient = connect(url, autocommit=True, options="-c lock_timeout=200ms")
    with impatient as ddl, pytest.raises(psycopg.errors.LockNotAvailable):
        ddl.execute(ALTER)  # gives up after 200 ms: retry later, when the long reader is gone

    # A reader is not blocked now. (If it were, it would raise QueryCanceled after the statement timeout.)
    with connect(url, autocommit=True, options="-c statement_timeout=5000ms") as newcomer:
        newcomer.execute("select 1 from accounts limit 1")
    long_reader.commit()
    long_reader.close()

    with connect(url, autocommit=True, options="-c lock_timeout=200ms") as ddl:
        ddl.execute(ALTER)  # with the reader gone the same statement succeeds at once
        assert (
            scalar(
                ddl,
                "select count(*) from information_schema.columns "
                "where table_name = 'accounts' and column_name = 'note'",
            )
            == 1
        )


def test_create_index_concurrently_cannot_run_inside_a_transaction(make_db: Callable[..., str]) -> None:
    url = make_db("07_migrations.sql")
    with connect(url) as in_tx, pytest.raises(psycopg.errors.ActiveSqlTransaction):
        in_tx.execute("create index concurrently accounts_qty on accounts (qty)")
    with connect(url, autocommit=True) as c:
        c.execute("create index concurrently accounts_qty on accounts (qty)")  # builds without blocking writes
        assert scalar(c, "select indisvalid from pg_index where indexrelid = 'accounts_qty'::regclass") is True
