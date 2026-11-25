"""Study 6: row-level security as a safety net for multi-tenant tables (not an authentication system)."""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from dataclasses import dataclass

import psycopg
import psycopg.errors
import pytest
from psycopg import sql

from patterns.db import ADMIN_URL, SQL_DIR, Conn, connect, scalar, temp_database, with_credentials

PASSWORD = "pw"


@dataclass
class Rls:
    owner_url: str  # the role that owns the table
    app_url: str  # the role the application connects as: not the owner, not a superuser


@pytest.fixture
def rls() -> Iterator[Rls]:
    suffix = secrets.token_hex(4)
    owner, app = f"owner_{suffix}", f"app_{suffix}"  # roles are cluster-wide, so they get unique names
    try:
        with temp_database() as url:
            with connect(url, autocommit=True) as admin:
                for role in (owner, app):
                    admin.execute(
                        sql.SQL("create role {} login password {}").format(sql.Identifier(role), sql.Literal(PASSWORD))
                    )
                admin.execute(sql.SQL("grant create on schema public to {}").format(sql.Identifier(owner)))
            owner_url = with_credentials(url, owner, PASSWORD)
            with connect(owner_url, autocommit=True) as o:
                o.execute((SQL_DIR / "06_rls.sql").read_text())  # table and policy, created by the owner
                o.execute(
                    "insert into notes (tenant, body) values "
                    "('a', 'a1'), ('a', 'a2'), ('a', 'a3'), ('b', 'b1'), ('b', 'b2')"
                )
                o.execute(sql.SQL("grant select, insert, update on notes to {}").format(sql.Identifier(app)))
                o.execute(sql.SQL("grant usage on sequence notes_id_seq to {}").format(sql.Identifier(app)))
            yield Rls(owner_url=owner_url, app_url=with_credentials(url, app, PASSWORD))
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            for role in (owner, app):
                admin.execute(sql.SQL("drop role if exists {}").format(sql.Identifier(role)))


def as_tenant(conn: Conn, tenant: str) -> None:
    """The per-transaction setting the policy reads. `true` means: forget it when the transaction ends."""
    conn.execute("select set_config('app.tenant', %s, true)", (tenant,))


def test_a_query_with_no_tenant_filter_still_returns_only_the_tenants_own_rows(rls: Rls) -> None:
    with connect(rls.app_url) as conn:
        as_tenant(conn, "a")
        rows = conn.execute("select tenant from notes").fetchall()  # note: no WHERE clause at all
        assert [r[0] for r in rows] == ["a", "a", "a"]
    with connect(rls.app_url) as conn:
        as_tenant(conn, "b")
        assert scalar(conn, "select count(*) from notes") == 2


def test_a_request_that_forgot_to_set_a_tenant_sees_nothing(rls: Rls) -> None:
    with connect(rls.app_url) as conn:
        assert scalar(conn, "select count(*) from notes") == 0  # fails closed, not open


def test_writing_a_row_for_another_tenant_is_refused(rls: Rls) -> None:
    with connect(rls.app_url) as conn:
        as_tenant(conn, "a")
        conn.execute("insert into notes (tenant, body) values ('a', 'mine')")  # fine
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("insert into notes (tenant, body) values ('b', 'smuggled')")


def test_a_row_cannot_be_moved_to_another_tenant(rls: Rls) -> None:
    with connect(rls.app_url) as conn:
        as_tenant(conn, "a")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("update notes set tenant = 'b' where body = 'a1'")


def test_the_table_owner_bypasses_the_policy_until_row_level_security_is_forced(rls: Rls) -> None:
    with connect(rls.owner_url, autocommit=True) as owner:
        assert scalar(owner, "select count(*) from notes") == 5  # the owner sees every tenant: a classic surprise
        owner.execute("alter table notes force row level security")
        assert scalar(owner, "select count(*) from notes") == 0  # now the policy applies to the owner too


def test_a_session_level_setting_leaks_to_the_next_user_of_a_pooled_connection(rls: Rls) -> None:
    with connect(rls.app_url) as pooled:
        pooled.execute("select set_config('app.tenant', 'a', false)")  # false = for the whole session: the mistake
        pooled.commit()
        # The connection goes back to the pool. The next request, for nobody in particular, reuses it:
        assert scalar(pooled, "select count(*) from notes") == 3  # it can read tenant a's rows


def test_a_transaction_level_setting_does_not_outlive_its_transaction(rls: Rls) -> None:
    with connect(rls.app_url) as pooled:
        with pooled.transaction():
            as_tenant(pooled, "a")
            assert scalar(pooled, "select count(*) from notes") == 3
        assert scalar(pooled, "select count(*) from notes") == 0  # the next use of this connection starts empty


def test_a_transaction_level_setting_does_not_erase_an_older_session_level_one(rls: Rls) -> None:
    """The limit of the safe pattern: it only helps if nothing set the value for the whole session."""
    with connect(rls.app_url) as pooled:
        pooled.execute("select set_config('app.tenant', 'a', false)")  # an earlier mistake, for the session
        pooled.commit()
        with pooled.transaction():
            as_tenant(pooled, "b")
            assert scalar(pooled, "select count(*) from notes") == 2  # b, while the transaction lasts
        assert scalar(pooled, "select count(*) from notes") == 3  # the old session value is back: tenant a again


def test_a_pool_must_reset_session_state_when_it_hands_a_connection_back(rls: Rls) -> None:
    # Autocommit: DISCARD ALL cannot run inside a transaction block (a pool runs it between checkouts).
    with connect(rls.app_url, autocommit=True) as pooled:
        pooled.execute("select set_config('app.tenant', 'a', false)")  # set for the whole session
        assert scalar(pooled, "select count(*) from notes") == 3
        pooled.execute("discard all")  # what a pool's reset step does (psycopg_pool, PgBouncer's server_reset_query)
        assert scalar(pooled, "select count(*) from notes") == 0
