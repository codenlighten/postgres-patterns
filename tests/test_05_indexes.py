"""Study 5: indexes, and proving the planner uses them (the plan is read, not assumed)."""

from __future__ import annotations

from collections.abc import Callable

from patterns.db import connect, scalar
from patterns.plans import explain, index_names, node_types

LOOKUP = "select id from orders where lower(email) = %s"


def test_an_expression_index_is_used_only_by_queries_written_the_same_way(
    make_db: Callable[..., str],
) -> None:
    with connect(make_db("05_indexes.sql"), autocommit=True) as c:
        before = explain(c, LOOKUP, ("user42@example.com",))
        assert "Seq Scan" in node_types(before)  # no index on lower(email): every row is read

        c.execute("create index orders_lower_email on orders (lower(email))")
        c.execute("analyze orders")
        after = explain(c, LOOKUP, ("user42@example.com",))
        assert "orders_lower_email" in index_names(after)
        assert "Seq Scan" not in node_types(after)

        # Same data, written differently: the index cannot be used, because it indexes lower(email), not email.
        plain = explain(c, "select id from orders where email = %s", ("User42@Example.com",))
        assert "orders_lower_email" not in index_names(plain)


def test_a_partial_index_is_a_fraction_of_the_size_and_serves_the_queries_it_was_built_for(
    make_db: Callable[..., str],
) -> None:
    with connect(make_db("05_indexes.sql"), autocommit=True) as c:
        c.execute("create index orders_created_all on orders (created_at)")
        c.execute("create index orders_open_created on orders (created_at) where status = 'open'")
        full = scalar(c, "select pg_relation_size('orders_created_all')")
        partial = scalar(c, "select pg_relation_size('orders_open_created')")
        assert partial * 20 < full  # about 1% of the rows are open

        c.execute("drop index orders_created_all")  # so the planner has to choose between the partial index and nothing
        c.execute("analyze orders")
        plan = explain(c, "select id from orders where status = 'open' order by created_at limit 20")
        assert "orders_open_created" in index_names(plan)

        # A query for a different status cannot use it.
        other = explain(c, "select id from orders where status = 'closed' order by created_at limit 20")
        assert "orders_open_created" not in index_names(other)


def test_including_the_selected_column_in_the_index_turns_a_table_lookup_into_an_index_only_scan(
    make_db: Callable[..., str],
) -> None:
    query = "select customer_id, total from orders where customer_id = 42"
    with connect(make_db("05_indexes.sql"), autocommit=True) as c:
        c.execute("create index orders_customer on orders (customer_id)")
        c.execute("vacuum analyze orders")
        plain = explain(c, query)
        assert "Index Only Scan" not in node_types(plain)  # the index finds the rows, the table supplies total
        assert "orders_customer" in index_names(plain)

        c.execute("create index orders_customer_total on orders (customer_id) include (total)")
        c.execute("drop index orders_customer")
        c.execute("vacuum analyze orders")  # sets the visibility map that index-only scans rely on
        covering = explain(c, query)
        assert "Index Only Scan" in node_types(covering)
        assert "orders_customer_total" in index_names(covering)
