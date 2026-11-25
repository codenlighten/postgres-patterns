"""Retrying a whole transaction when PostgreSQL asks for it.

Under SERIALIZABLE (and on deadlocks) PostgreSQL may abort a transaction that did nothing wrong, to keep
the outcome equal to some one-at-a-time order. The contract is: run the *entire* transaction again.
"""

from __future__ import annotations

from collections.abc import Callable

import psycopg.errors

RETRYABLE = (psycopg.errors.SerializationFailure, psycopg.errors.DeadlockDetected)


def retry_transaction[T](fn: Callable[[], T], *, attempts: int = 5) -> T:
    """Calls ``fn`` (which must run one complete transaction and roll back if it raises) up to ``attempts`` times."""
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except RETRYABLE:
            if attempt == attempts:
                raise
    raise AssertionError("unreachable")  # the loop always returns or raises
