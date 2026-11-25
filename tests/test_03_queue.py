"""Study 3: a job queue in a table."""

from __future__ import annotations

import threading
from collections.abc import Callable

from patterns.db import connect, scalar
from patterns.queue import claim_batch


def test_eight_workers_claim_every_job_exactly_once(make_db: Callable[..., str]) -> None:
    url = make_db("03_queue.sql")
    claimed: list[int] = []
    lock = threading.Lock()

    def worker(name: str) -> None:
        with connect(url) as conn:
            while True:
                batch = claim_batch(conn, name, 5)
                if not batch:
                    return
                with lock:
                    claimed.extend(batch)

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert len(claimed) == 200  # nothing was missed...
    assert len(set(claimed)) == 200  # ...and nothing was handed out twice
    with connect(url, autocommit=True) as c:
        assert scalar(c, "select count(*) from jobs where state = 'queued'") == 0


def test_a_worker_is_never_blocked_by_rows_another_worker_holds(make_db: Callable[..., str]) -> None:
    url = make_db("03_queue.sql")
    slow = connect(url)
    fast = connect(url, options="-c lock_timeout=200ms")  # if the claim waited, this would raise instead
    held = [r[0] for r in slow.execute("select id from jobs where state = 'queued' order by id limit 5 for update")]
    got = claim_batch(fast, "fast", 5)  # the slow worker still holds its rows' locks
    assert len(got) == 5
    assert not set(got) & set(held)  # it was handed the next five, not the locked ones
    slow.rollback()
    slow.close()
    fast.close()


def test_select_then_update_hands_the_same_jobs_to_two_workers(make_db: Callable[..., str]) -> None:
    """The version people write first. Both workers read the same five rows before either marks them."""
    url = make_db("03_queue.sql")
    a, b = connect(url), connect(url)
    ids_a = [r[0] for r in a.execute("select id from jobs where state = 'queued' order by id limit 5")]
    ids_b = [r[0] for r in b.execute("select id from jobs where state = 'queued' order by id limit 5")]
    a.execute("update jobs set state = 'running', claimed_by = 'a' where id = any(%s)", (ids_a,))
    a.commit()
    b.execute("update jobs set state = 'running', claimed_by = 'b' where id = any(%s)", (ids_b,))
    b.commit()
    assert ids_a == ids_b  # the same five jobs would now run twice
    a.close()
    b.close()


def test_the_queued_index_stays_tiny_however_many_finished_jobs_pile_up(make_db: Callable[..., str]) -> None:
    url = make_db("03_queue.sql")
    with connect(url, autocommit=True) as c:
        c.execute("insert into jobs (payload, state) select 'old ' || g, 'done' from generate_series(1, 50000) as g")
        c.execute("vacuum analyze jobs")
        queued_index = scalar(c, "select pg_relation_size('jobs_queued')")
        primary_key = scalar(c, "select pg_relation_size('jobs_pkey')")
        assert scalar(c, "select count(*) from jobs where state = 'queued'") == 200
    # 200 queued rows against 50,200 in total: the partial index only holds the queued ones.
    assert queued_index * 10 < primary_key
