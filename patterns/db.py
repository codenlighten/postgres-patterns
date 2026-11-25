"""Throwaway databases, so every test starts from a clean schema and tests cannot see each other."""

from __future__ import annotations

import os
import secrets
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

# A superuser connection to a server you can create databases on. `docker compose up -d --wait` gives
# exactly this; set TEST_DATABASE_URL to use another server.
# What psycopg accepts as query parameters.
Params = Sequence[Any] | Mapping[str, Any] | None

# The type of every connection in this repository (rows come back as plain tuples).
Conn = psycopg.Connection[tuple[Any, ...]]

ADMIN_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54333/postgres")
SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def with_database(url: str, name: str) -> str:
    return make_conninfo(url, dbname=name)


def with_credentials(url: str, user: str, password: str) -> str:
    return make_conninfo(url, user=user, password=password)


@contextmanager
def temp_database() -> Iterator[str]:
    """Creates a database, yields its connection string, and drops it (closing stragglers) afterwards."""
    name = f"t_{secrets.token_hex(6)}"
    try:
        admin = psycopg.connect(ADMIN_URL, autocommit=True)
    except psycopg.OperationalError as err:
        raise RuntimeError(
            f"Cannot reach PostgreSQL at {ADMIN_URL}. "
            f"Run `docker compose up -d --wait` or set TEST_DATABASE_URL. ({err})"
        ) from err
    with admin:
        admin.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
    try:
        yield with_database(ADMIN_URL, name)
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            admin.execute(sql.SQL("drop database {} with (force)").format(sql.Identifier(name)))


def load(url: str, *files: str) -> None:
    """Runs whole SQL files from ``sql/`` (several statements each) against a database."""
    with psycopg.connect(url, autocommit=True) as conn:
        for name in files:
            conn.execute((SQL_DIR / name).read_text())


def connect(
    url: str,
    *,
    isolation: psycopg.IsolationLevel | None = None,
    autocommit: bool = False,
    options: str | None = None,
) -> Conn:
    """A connection that is NOT in autocommit mode by default, so a test controls when transactions end.

    ``options`` sets server settings for the whole session, for example ``-c lock_timeout=200ms``. (A plain
    ``SET`` inside a transaction would be undone by the rollback that follows an error.)
    """
    if options is not None:
        url = make_conninfo(url, options=options)
    conn = psycopg.connect(url, autocommit=autocommit)
    if isolation is not None:
        conn.isolation_level = isolation
    return conn


def scalar(conn: Conn, query: str, params: Params = None) -> Any:
    """The first column of the first row of a query that must return one."""
    row = conn.execute(query, params).fetchone()
    assert row is not None, f"no row returned by: {query}"
    return row[0]
