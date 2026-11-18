-- =====================================================================
-- OPTIONAL - needs the FAR owner's approval (it adds indexes to FAR's tables).
-- Today FAR enforces these "no duplicates" rules only in its application code.
-- With two applications writing the same tables, the database should enforce them.
-- Run as postgres (or far_app) on company_db. Adds indexes only; changes no data.
-- =====================================================================

-- Step A: look for existing duplicates. If any query returns rows, clean them up in FAR
-- first (the matching index in step B would fail on that table).
SELECT lower(btrim(name)) AS partner_name, count(*) FROM core.partners
GROUP BY 1 HAVING count(*) > 1;

SELECT lower(btrim(employee_code)) AS employee_code, count(*) FROM core.assignees
WHERE type = 'employee' AND employee_code IS NOT NULL
GROUP BY 1 HAVING count(*) > 1;

SELECT upper(btrim(name)) AS department, count(*) FROM core.departments
GROUP BY 1 HAVING count(*) > 1;

SELECT upper(btrim(name)) AS branch, count(*) FROM core.branches
GROUP BY 1 HAVING count(*) > 1;

-- Step B: case-insensitive uniqueness, matching FAR's own application rules.
-- CONCURRENTLY = no table lock; run each statement on its own (not inside a transaction).
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS partners_name_ci_key
    ON core.partners (lower(btrim(name)));
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS assignees_employee_code_ci_key
    ON core.assignees (lower(btrim(employee_code))) WHERE type = 'employee';
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS departments_name_ci_key
    ON core.departments (upper(btrim(name)));
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS branches_name_ci_key
    ON core.branches (upper(btrim(name)));
