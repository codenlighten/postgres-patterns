"""Claiming jobs from a table (study 3). The SQL is the point, so it is shown in full."""

from __future__ import annotations

from patterns.db import Conn

# One statement: pick the next queued jobs nobody else has locked, and mark them running.
#   FOR UPDATE   locks the picked rows until this transaction ends;
#   SKIP LOCKED  silently passes over rows another transaction has row-locked, instead of waiting for them.
CLAIM = """
with next as (
    select id from jobs
    where state = 'queued'
    order by id
    limit %(n)s
    for update skip locked
)
update jobs j
set state = 'running', claimed_by = %(worker)s, claimed_at = now()
from next
where j.id = next.id
returning j.id
"""


def claim_batch(conn: Conn, worker: str, n: int) -> list[int]:
    """Claims up to ``n`` jobs for ``worker`` in one transaction and returns their ids."""
    with conn.transaction():
        rows = conn.execute(CLAIM, {"n": n, "worker": worker}).fetchall()
    return sorted(int(r[0]) for r in rows)
