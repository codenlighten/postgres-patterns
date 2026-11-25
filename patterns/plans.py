"""Reading PostgreSQL query plans, so a test can check what the planner chose."""

from __future__ import annotations

from typing import Any

from patterns.db import Conn, Params


def explain(conn: Conn, query: str, params: Params = None) -> dict[str, Any]:
    """The plan of ``query`` as a nested dict (``EXPLAIN (FORMAT JSON)``); the query is planned, not run."""
    row = conn.execute("explain (format json) " + query, params).fetchone()
    assert row is not None
    return row[0][0]["Plan"]  # type: ignore[no-any-return]


def nodes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Every node of a plan, parents before children."""
    out = [plan]
    for child in plan.get("Plans", []):
        out.extend(nodes(child))
    return out


def node_types(plan: dict[str, Any]) -> set[str]:
    return {n["Node Type"] for n in nodes(plan)}


def index_names(plan: dict[str, Any]) -> set[str]:
    return {n["Index Name"] for n in nodes(plan) if "Index Name" in n}
