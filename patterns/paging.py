"""Three ways to read a feed page by page (study 4), newest first."""

from __future__ import annotations

from datetime import datetime

from patterns.db import Conn


def offset_page(conn: Conn, offset: int, size: int) -> list[int]:
    """Position-based: skips ``offset`` rows. Rows inserted meanwhile shift every later page."""
    rows = conn.execute(
        "select id from events order by created_at desc, id desc limit %s offset %s", (size, offset)
    ).fetchall()
    return [int(r[0]) for r in rows]


def keyset_page(conn: Conn, after: tuple[datetime, int] | None, size: int) -> list[tuple[datetime, int]]:
    """Cursor-based: rows strictly after ``(created_at, id)``. Stable, and served from the index."""
    if after is None:
        rows = conn.execute("select created_at, id from events order by created_at desc, id desc limit %s", (size,))
    else:
        rows = conn.execute(
            "select created_at, id from events where (created_at, id) < (%s, %s) "
            "order by created_at desc, id desc limit %s",
            (after[0], after[1], size),
        )
    return [(r[0], int(r[1])) for r in rows.fetchall()]


def keyset_page_without_tiebreak(conn: Conn, after: datetime | None, size: int) -> list[tuple[datetime, int]]:
    """The common mistake: paging by timestamp alone. Rows sharing the last timestamp are skipped."""
    if after is None:
        rows = conn.execute("select created_at, id from events order by created_at desc, id desc limit %s", (size,))
    else:
        rows = conn.execute(
            "select created_at, id from events where created_at < %s order by created_at desc, id desc limit %s",
            (after, size),
        )
    return [(r[0], int(r[1])) for r in rows.fetchall()]
