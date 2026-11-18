-- =====================================================================
-- Expense Portal <-> FAR common master data: one-time access setup.
-- Run as the postgres superuser, on company_db:
--   psql -h localhost -p 5433 -U postgres -d company_db -f 01_grant_portal_access.sql
-- Safe to re-run. Changes NO data and NO FAR table structure; only grants.
-- =====================================================================

-- The portal's database login (already exists on this server).
GRANT CONNECT ON DATABASE company_db TO expense_admin;

-- 1) The portal's own tables live in the `expense` schema FAR reserved for it.
GRANT USAGE, CREATE ON SCHEMA expense TO expense_admin;

-- 2) FAR's shared masters: read + write in place, so both apps see the same rows.
--    No DELETE: the portal only ever sets status = 'Inactive', exactly like FAR.
GRANT USAGE ON SCHEMA core TO expense_admin;
GRANT SELECT, INSERT, UPDATE, REFERENCES
    ON core.departments, core.branches, core.partners, core.assignees
    TO expense_admin;
GRANT USAGE, SELECT
    ON SEQUENCE core.departments_id_seq, core.branches_id_seq,
                core.partners_id_seq, core.assignees_id_seq
    TO expense_admin;

-- Check: every value below should be true.
SELECT has_schema_privilege('expense_admin', 'expense', 'CREATE')                AS expense_create,
       has_table_privilege('expense_admin', 'core.departments', 'SELECT,INSERT,UPDATE') AS departments,
       has_table_privilege('expense_admin', 'core.branches', 'SELECT,INSERT,UPDATE')    AS branches,
       has_table_privilege('expense_admin', 'core.partners', 'SELECT,INSERT,UPDATE')    AS partners,
       has_table_privilege('expense_admin', 'core.assignees', 'SELECT,INSERT,UPDATE')   AS assignees,
       NOT has_table_privilege('expense_admin', 'core.partners', 'DELETE')              AS no_delete;
