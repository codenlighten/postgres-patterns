# postgres-patterns: seven PostgreSQL studies, each demonstrated by a test

[![CI](https://github.com/codenlighten/postgres-patterns/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/codenlighten/postgres-patterns/actions/workflows/ci.yml)

Runnable studies of how PostgreSQL actually behaves when it matters: constraints, concurrency anomalies, job queues, pagination while data changes, indexes, row-level security and migrations on a live table. Each study is a short, commented SQL file plus tests that demonstrate the problem, show the fix, and fail if the fix is removed. Two of them come with measured timings.

It shows database engineering as evidence: not "use an index" but "this is the plan the planner chose, this is the size, this is the time, and here is what I could not show".

## Run it

Needs Python 3.12 and Docker.

```bash
docker compose up -d --wait            # PostgreSQL 16 on 127.0.0.1:54333
pip install -e ".[dev]"
pytest                                 # 45 tests, about 8 seconds
python -m bench.run                    # the timings below, a few seconds
```

Set `TEST_DATABASE_URL` to use another server (a superuser connection; every test makes and drops its own throwaway database). The default `postgres`/`postgres` credentials belong to the local throwaway container from `docker-compose.yml` and are not meant for anything else.

## The studies

| # | Study | What the tests demonstrate |
|---|---|---|
| 1 | [Constraints](sql/01_constraints.sql) | An exclusion constraint refuses overlapping bookings of one room, however the inserts race; back-to-back bookings are allowed; empty, unbounded and `infinity` ranges are refused. Under a 16-way race a plain `INSERT` can deadlock, and `ON CONFLICT DO NOTHING` does not (numbers below). |
| 2 | [Isolation](sql/02_isolation.sql) | A read-modify-write loses an update at READ COMMITTED; `SET n = n + 1` or `SELECT ... FOR UPDATE` fixes it. "Someone must stay on call" breaks under REPEATABLE READ (write skew) and holds under SERIALIZABLE, where the aborted transaction is retried and sees the new state. |
| 3 | [Job queue](sql/03_queue.sql) | Eight workers claim 200 jobs: every job once, none twice. A worker is not blocked by rows another worker holds. The naive select-then-update hands the same jobs to two workers. A partial index stays tiny as finished jobs pile up. |
| 4 | [Pagination](sql/04_pagination.sql) | OFFSET pages repeat rows when new rows arrive between pages; keyset pages never repeat or skip. Paging by timestamp alone silently skips rows that share a timestamp. |
| 5 | [Indexes](sql/05_indexes.sql) | The query plan is read, not assumed: an expression index is used only by queries written the same way; a partial index is under 1/20 of the size and serves its own queries; an `INCLUDE` index turns a table lookup into an index-only scan. |
| 6 | [Row-level security](sql/06_rls.sql) | A query with no tenant filter returns only the tenant's own rows; a request that forgot to set a tenant sees nothing; cross-tenant writes are refused; the table owner bypasses the policy until it is forced; a session-level setting leaks across a pooled connection, a transaction-level one does not (with a limit, see below). |
| 7 | [Migrations](sql/07_migrations.sql) | Adding a column with a constant default does not rewrite the table; changing a type does. A waiting `ALTER TABLE` blocks later readers; `lock_timeout` makes it give up instead. `CREATE INDEX CONCURRENTLY` cannot run in a transaction. |

## Measured results

PostgreSQL 16.15 in Docker on an AMD Ryzen 7 260 (Linux, `performance` governor, Python 3.12.12 client), `shared_buffers` 128 MB, run on 2026-11-25. Each row is the median of 7 runs after one warm-up, in milliseconds for the whole round trip including fetching the page; the machine had no other load of note (load average 0.3 before the first run). The benchmark was run twice; the two medians are shown as "run 1 / run 2". Small values (0.1 ms) are dominated by the client and are close to the noise.

**Pagination: a page of 20 from 1,000,000 events**

| Page starts after row | OFFSET | keyset |
|---|---|---|
| 0 | 0.10 / 0.13 | 0.09 / 0.09 |
| 100,000 | 3.59 / 3.68 | 0.14 / 0.24 |
| 500,000 | 18.97 / 18.33 | 0.19 / 0.21 |
| 900,000 | 34.23 / 33.91 | 0.19 / 0.20 |

OFFSET cost grows with how deep the page is, because PostgreSQL still walks and discards every skipped row. Keyset cost stays flat because it starts from an index position.

**Indexes: 200,000 orders**

| Query | Setup | median ms (run 1 / run 2) | Reading |
|---|---|---|---|
| `where lower(email) = ?` | no index | 20.76 / 20.65 | |
| | expression index | 0.10 / 0.09 | about 200 times faster |
| 20 oldest open orders (1% of rows) | full index on `created_at` | 0.31 / 0.30 | |
| | partial index (open only) | 0.24 / 0.20 | **Inconclusive on time:** the fastest-to-slowest ranges overlap. What is shown is size: a test asserts the partial index is under 1/20 of the full one. |
| Totals for one `customer_id` | index on `customer_id` | 0.22 / 0.20 | |
| | covering index (`INCLUDE total`) | 0.25 / 0.19 | **No difference measured.** The plan does change to an index-only scan (asserted by a test), but with this table fully in memory the time did not move. |

**Racing 16 sessions for one booking slot, 50 races per method (800 attempts each)**

| Method | Races with at least one deadlock victim | Clean "refused" answers | Total time |
|---|---|---|---|
| plain `INSERT` (deadlock timeout set to 100 ms) | 47 of 50 | 45 of 750 losers | 75.7 s |
| `INSERT ... ON CONFLICT DO NOTHING` | 0 of 50 | 750 of 750 losers | 4.4 s |

In every race exactly one booking was made: the constraint always holds. The difference is how the losers find out. With a plain insert, sessions that wait on each other's uncommitted rows form wait cycles and PostgreSQL aborts victims with "deadlock detected", after waiting `deadlock_timeout` (1 s by default). `ON CONFLICT DO NOTHING` reports a conflict as "no row returned" instead of raising. This is an observation about this single-slot race, not a general guarantee: if you use a plain insert on a hot slot, retry deadlocks (with a pause that differs per session: retrying in lockstep collided again and again when I tried it).

These numbers compare methods on one machine. They do not predict your production latency.

## What running it found

Most of the interesting results came from running it, not from planning it:

- **My first concurrency test was wrong about PostgreSQL.** I expected 15 clean "overlap" errors from 16 racing inserts. Instead many died with `deadlock detected`, and retrying them without any pause deadlocked again in lockstep, taking 160 seconds. The study now shows both the plain insert and `ON CONFLICT DO NOTHING`, and the numbers above.
- **A broken cursor hung a test instead of failing it.** With keyset paging mistakenly including the cursor row, every page repeated forever. The walks are now bounded and fail with a clear message.
- **A first "partial index is smaller" test could not fail:** at 200 rows both indexes are one page. It now uses 50,000 finished jobs and compares against the primary-key index.
- Lint and strict type-checking found their usual small things; all fixed.

## Independent review

An independent AI review (a different model, asked to find factual errors about PostgreSQL behaviour and misleading claims) found eight real problems, all fixed:

| Finding | Fix |
|---|---|
| The row-level-security study claimed isolation "enforced by the database", but the application role can set the tenant to anything | The comments now state the trust boundary: this protects against a forgotten filter, not against a role that can run arbitrary SQL |
| A transaction-level setting does not erase an earlier session-level one; the old value reappears | New test showing it, and a test that a pool's reset (`DISCARD ALL`) clears the session value |
| The range check let `infinity` bounds through (`lower_inf` only detects unbounded ranges) | `isfinite` checks added; two new test cases; a deliberate break for it |
| "UNIQUE and foreign keys stop one bad row" was wrong | Reworded: CHECK tests a row; UNIQUE and foreign keys enforce relationships; exclusion generalises the conflict test |
| "No row lock can protect against write skew" was wrong | Reworded: locking the rows you read also works, but every code path must remember to |
| The `ON CONFLICT` explanation overpromised, and an `elapsed < 5` assertion could not show the absence of deadlocks | Explanation narrowed to what was observed; the timing assertion removed; the frequency measured instead (table above) |
| Several concurrency tests asserted wall-clock thresholds or ignored worker exceptions | Worker errors are re-raised, threads are checked to have finished, timeouts are hang guards only, no performance thresholds |
| Comments overstated lock queues ("every query") and the queue's guarantees | Qualified: queries needing a conflicting lock queue behind the ALTER; `SKIP LOCKED` skips row locks only and is not strict FIFO |

## Deliberate breaks

Each change was made on its own to the SQL or Python code, the whole suite run, and the code restored (45 of 45 pass afterwards). Every break failed the tests named here.

| Break | Failed |
|---|---|
| Exclusion constraint removed | the overlap test and both race tests |
| Exclusion compares ranges for equality instead of overlap | `test_overlapping_bookings_of_one_room_are_refused` |
| Range sanity check removed | 5 bad-row cases |
| Range check no longer rejects infinite timestamps | the two `infinity` cases |
| Claim query without `SKIP LOCKED` | `test_a_worker_is_never_blocked_by_rows_another_worker_holds` |
| Queue index no longer partial | `test_the_queued_index_stays_tiny...` |
| Keyset page includes the cursor row (`<=`) | both keyset walks (each fails in about 8 s instead of hanging) |
| Row-level security not enabled | 9 tests |
| Policy lets every row be read | 7 tests |
| Policy lets every row be written | `test_writing_a_row_for_another_tenant_is_refused` |
| Retry helper retries nothing | 2 tests |
| Half the orders are 'open' | the partial-index size test |
| Plan reader ignores child nodes | 3 index tests |

## Limits

- One PostgreSQL version (16) and one machine for the timings. Behaviour such as plan choice and lock details can differ in other versions.
- The concurrency tests use real threads and real timing, so they are not deterministic by construction. They passed three runs in a row on this machine (6.6 to 8.1 s each), but a different machine could surface a different interleaving.
- Row-level security is a safety net against a forgotten filter, not tenant authentication. A role that can run arbitrary SQL can set any tenant.
- The tables are small enough to sit in memory, so the benchmark says nothing about disk-bound behaviour, and the covering-index and partial-index time differences were not measurable here.
- Nothing here covers replication, partitioning, connection pooling products, backup or security hardening.
- It is a set of studies, not a library to depend on.

## Layout

```
sql/        one commented SQL file per study (the point of the repository)
patterns/   small helpers: throwaway databases, plan reading, the queue claim, paging, transaction retry
tests/      the proofs; tests/test_0N_*.py matches sql/0N_*.sql
bench/      the timings above
```

## Dependencies

`psycopg` (the PostgreSQL driver) at run time; `pytest`, `ruff` and `mypy` for development. All are public packages; none of their code is copied here.

## Licence

MIT. See `LICENSE`.
