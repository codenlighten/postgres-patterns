"""Timings for studies 4 and 5. Prints Markdown tables: median, fastest and slowest of N runs, in ms.

    python -m bench.run            # about a minute; needs the database from `docker compose up -d --wait`

Numbers depend on the machine: the header says which one this was. They are for comparing the methods on
the same machine, not for predicting your production latency.
"""

from __future__ import annotations

import platform
import statistics
import time
from collections.abc import Callable

from patterns.db import Conn, connect, load, scalar, temp_database

RUNS = 7
PAGE = 20
ROWS = 1_000_000


def timed(fn: Callable[[], object], runs: int = RUNS) -> tuple[float, float, float]:
    """(median, min, max) in milliseconds over ``runs`` runs, after one warm-up run."""
    fn()
    samples = []
    for _ in range(runs):
        t = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t) * 1000)
    return statistics.median(samples), min(samples), max(samples)


def line(*labels: str, stats: tuple[float, float, float]) -> str:
    """One Markdown table row: the labels, then median, min and max."""
    return "| " + " | ".join(labels) + f" | {stats[0]:.2f} | {stats[1]:.2f} | {stats[2]:.2f} |"


def header(conn: Conn) -> None:
    print(f"- PostgreSQL: {scalar(conn, 'show server_version')}, shared_buffers {scalar(conn, 'show shared_buffers')}")
    print(
        f"- Client: Python {platform.python_version()}, {platform.machine()}, {platform.system()} {platform.release()}"
    )
    print(
        f"- {RUNS} runs per row after one warm-up; milliseconds for the whole round trip, including fetching the page\n"
    )


def pagination() -> None:
    with temp_database() as url:
        load(url, "04_pagination.sql")
        with connect(url, autocommit=True) as c:
            c.execute(
                "insert into events (created_at, payload) "
                "select timestamptz '2026-01-01' + g * interval '1 second', 'x' from generate_series(1, %s) g",
                (ROWS,),
            )
            c.execute("vacuum analyze events")
            print(f"## Pagination: page of {PAGE} from a table of {ROWS:,} events\n")
            header(c)
            print("| Page starts after | Method | median ms | min | max |")
            print("|---|---|---|---|---|")
            for depth in (0, 100_000, 500_000, 900_000):
                off = timed(
                    lambda d=depth: c.execute(  # type: ignore[misc]
                        "select id from events order by created_at desc, id desc limit %s offset %s",
                        (PAGE, d),
                    ).fetchall()
                )
                if depth == 0:
                    key = timed(
                        lambda: c.execute(
                            "select created_at, id from events order by created_at desc, id desc limit %s",
                            (PAGE,),
                        ).fetchall()
                    )
                else:
                    cursor = c.execute(
                        "select created_at, id from events order by created_at desc, id desc limit 1 offset %s",
                        (depth - 1,),
                    ).fetchone()
                    assert cursor is not None
                    key = timed(
                        lambda cur=cursor: c.execute(  # type: ignore[misc]
                            "select created_at, id from events where (created_at, id) < (%s, %s) "
                            "order by created_at desc, id desc limit %s",
                            (cur[0], cur[1], PAGE),
                        ).fetchall()
                    )
                print(line(f"row {depth:,}", "OFFSET", stats=off))
                print(line(f"row {depth:,}", "keyset", stats=key))
            print()


def indexes() -> None:
    with temp_database() as url:
        load(url, "05_indexes.sql")
        with connect(url, autocommit=True) as c:
            print("## Indexes: 200,000 orders\n")
            header(c)
            print("| Query | Setup | median ms | min | max |")
            print("|---|---|---|---|---|")

            def lookup() -> object:
                return c.execute("select id from orders where lower(email) = %s", ("user42@example.com",)).fetchall()

            print(line("`where lower(email) = ?`", "no index", stats=timed(lookup)))
            c.execute("create index orders_lower_email on orders (lower(email))")
            c.execute("analyze orders")
            print(line("`where lower(email) = ?`", "expression index", stats=timed(lookup)))

            def open_newest() -> object:
                return c.execute("select id from orders where status = 'open' order by created_at limit 20").fetchall()

            c.execute("create index orders_created_all on orders (created_at)")
            c.execute("analyze orders")
            print(line("20 oldest open orders", "full index on created_at", stats=timed(open_newest)))
            c.execute("create index orders_open_created on orders (created_at) where status = 'open'")
            c.execute("drop index orders_created_all")
            c.execute("analyze orders")
            print(line("20 oldest open orders", "partial index (open only)", stats=timed(open_newest)))

            def customer_total() -> object:
                return c.execute("select customer_id, total from orders where customer_id = 42").fetchall()

            c.execute("create index orders_customer on orders (customer_id)")
            c.execute("vacuum analyze orders")
            print(line("one customer's totals", "index on customer_id", stats=timed(customer_total)))
            c.execute("create index orders_customer_total on orders (customer_id) include (total)")
            c.execute("drop index orders_customer")
            c.execute("vacuum analyze orders")
            print(line("one customer's totals", "covering index (INCLUDE total)", stats=timed(customer_total)))
            print()


if __name__ == "__main__":
    pagination()
    indexes()
