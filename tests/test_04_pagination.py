"""Study 4: pagination while the data changes."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from patterns.db import Conn, connect
from patterns.paging import keyset_page, keyset_page_without_tiebreak, offset_page

BASE = datetime(2026, 3, 1, tzinfo=UTC)


def _insert(conn_url: str, stamps: list[datetime]) -> None:
    with connect(conn_url, autocommit=True) as c:
        for i, ts in enumerate(stamps):
            c.execute("insert into events (created_at, payload) values (%s, %s)", (ts, f"event {i}"))


MAX_PAGES = 100  # reading 50 rows takes a handful of pages; more means the cursor is not advancing


def _walk_keyset(
    reader: Conn, size: int, after_first_page: Callable[[], None] | None = None
) -> list[tuple[datetime, int]]:
    """Reads every row by keyset paging. Gives up (and fails) rather than loop forever on a broken cursor."""
    seen: list[tuple[datetime, int]] = []
    cursor = None
    for page_no in range(MAX_PAGES):
        page = keyset_page(reader, cursor, size)
        if not page:
            return seen
        seen += page
        cursor = page[-1]
        if page_no == 0 and after_first_page is not None:
            after_first_page()
    raise AssertionError("keyset walk never reached an empty page: the cursor is not advancing")


def _original_ids(url: str) -> list[int]:
    with connect(url, autocommit=True) as c:
        return [int(r[0]) for r in c.execute("select id from events order by created_at desc, id desc")]


def test_offset_pages_repeat_rows_when_new_rows_arrive_between_pages(make_db: Callable[..., str]) -> None:
    url = make_db("04_pagination.sql")
    _insert(url, [BASE + timedelta(minutes=i) for i in range(50)])
    with connect(url, autocommit=True) as reader:
        seen = offset_page(reader, 0, 10)  # page 1: the newest ten
        _insert(url, [BASE + timedelta(minutes=100 + i) for i in range(5)])  # five newer rows arrive
        seen += offset_page(reader, 10, 10)  # page 2
    assert len(seen) == 20
    assert len(set(seen)) == 15  # five rows from page 1 appear again on page 2


def test_keyset_pages_never_repeat_or_skip_when_new_rows_arrive(make_db: Callable[..., str]) -> None:
    url = make_db("04_pagination.sql")
    _insert(url, [BASE + timedelta(minutes=i) for i in range(50)])
    expected = _original_ids(url)
    with connect(url, autocommit=True) as reader:
        seen = _walk_keyset(
            reader, 10, after_first_page=lambda: _insert(url, [BASE + timedelta(minutes=100 + i) for i in range(5)])
        )  # the same interruption as above
    assert [i for _, i in seen] == expected  # every original row, once, in order; newer rows are ahead of the cursor


def test_paging_by_timestamp_alone_skips_rows_that_share_a_timestamp(make_db: Callable[..., str]) -> None:
    url = make_db("04_pagination.sql")
    stamps = [BASE + timedelta(minutes=group) for group in range(5) for _ in range(10)]  # 5 groups of 10 ties
    _insert(url, stamps)

    broken: list[int] = []
    with connect(url, autocommit=True) as reader:
        after = None
        for _ in range(MAX_PAGES):
            page = keyset_page_without_tiebreak(reader, after, 7)
            if not page:
                break
            broken += [i for _, i in page]
            after = page[-1][0]
    assert len(broken) < 50  # rows that tie with the last row of a page are never shown

    with connect(url, autocommit=True) as reader:
        correct = [i for _, i in _walk_keyset(reader, 7)]
    assert sorted(correct) == sorted(_original_ids(url))
    assert len(correct) == len(set(correct)) == 50
