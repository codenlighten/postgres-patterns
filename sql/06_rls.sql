-- Study 6: row-level security as a safety net for multi-tenant tables.
--
-- WHAT IT PROTECTS AGAINST: application code that forgets a tenant filter. Every query on this connection
-- is filtered by the policy, even `select * from notes` with no WHERE.
-- WHAT IT DOES NOT PROTECT AGAINST: the application role choosing the tenant. The tenant comes from a
-- setting that the application sets, and a role that can run arbitrary SQL (for example through SQL
-- injection) can set it to any tenant. If the database must enforce who the tenant is, derive it from a
-- database-authenticated identity that the role cannot change, such as a separate login role per tenant.
--
-- The setting is applied *per transaction* (`set_config(..., true)`), which is the safe way to use it
-- with a connection pool: the value disappears when the transaction ends. (It does not erase a value that
-- was set earlier for the whole session; see the tests.)
--
-- Roles are created by the tests (they are cluster-wide, so they need unique names). This file only
-- defines the table and the policy, and is run by the table's owner.

create table notes (
    id     bigint generated always as identity primary key,
    tenant text not null check (tenant <> ''),
    body   text not null
);

alter table notes enable row level security;

-- Rows are visible, and writable, only when they belong to the tenant named in the transaction's setting.
-- `current_setting(..., true)` returns NULL or '' (not an error) when nothing was set, and neither equals
-- a real tenant, so a request that forgot to set a tenant sees nothing: the policy fails closed.
create policy tenant_isolation on notes
    using      (tenant = current_setting('app.tenant', true))
    with check (tenant = current_setting('app.tenant', true));
