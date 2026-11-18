-- Replica of FAR's shared master tables (company_db schema `core`), taken from
-- FAR-master-data-report.md (FAR migrations 001-025). Used ONLY to build a throwaway
-- schema for tests and migration generation; FAR's real tables are never created here.
-- __CORE__ is replaced with the target schema name.

CREATE SCHEMA __CORE__;

CREATE TABLE __CORE__.users (
    id serial PRIMARY KEY,
    name text NOT NULL,
    email text NOT NULL UNIQUE,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE __CORE__.departments (
    id serial PRIMARY KEY,
    name text NOT NULL CONSTRAINT departments_name_key UNIQUE,
    status text NOT NULL DEFAULT 'Active'
        CONSTRAINT departments_status_check CHECK (status = ANY (ARRAY['Active', 'Inactive'])),
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by integer REFERENCES __CORE__.users(id),
    updated_at timestamptz,
    updated_by integer REFERENCES __CORE__.users(id)
);

CREATE TABLE __CORE__.branches (
    id serial PRIMARY KEY,
    name text NOT NULL CONSTRAINT branches_name_key UNIQUE,
    code integer NOT NULL CONSTRAINT branches_code_key UNIQUE,
    address text,
    status text NOT NULL DEFAULT 'Active'
        CONSTRAINT branches_status_check CHECK (status = ANY (ARRAY['Active', 'Inactive'])),
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by integer REFERENCES __CORE__.users(id),
    updated_at timestamptz,
    updated_by integer REFERENCES __CORE__.users(id)
);

CREATE TABLE __CORE__.partners (
    id serial PRIMARY KEY,
    type text NOT NULL DEFAULT 'Vendor'
        CONSTRAINT partners_type_check CHECK (type = ANY (ARRAY['Vendor', 'Contractor'])),
    name text NOT NULL,
    department_id integer REFERENCES __CORE__.departments(id),
    contact_info text,
    description text,
    status text NOT NULL DEFAULT 'Active'
        CONSTRAINT partners_status_check CHECK (status = ANY (ARRAY['Active', 'Inactive'])),
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by integer REFERENCES __CORE__.users(id),
    updated_at timestamptz,
    updated_by integer REFERENCES __CORE__.users(id)
);

CREATE TABLE __CORE__.assignees (
    id serial PRIMARY KEY,
    type text NOT NULL
        CONSTRAINT assignees_type_check CHECK (type = ANY (ARRAY['employee', 'partner'])),
    name text NOT NULL,
    employee_code text,
    company_name text,
    department_id integer REFERENCES __CORE__.departments(id),
    email text,
    status text NOT NULL DEFAULT 'Active'
        CONSTRAINT assignees_status_check CHECK (status = ANY (ARRAY['Active', 'Inactive'])),
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by integer REFERENCES __CORE__.users(id),
    updated_at timestamptz,
    updated_by integer REFERENCES __CORE__.users(id),
    CONSTRAINT assignees_code_vs_company_check CHECK (
        (type = 'employee' AND company_name IS NULL) OR (type = 'partner' AND employee_code IS NULL)
    )
);

-- Sample FAR data (fictional), shaped like the real rows in the report.
INSERT INTO __CORE__.departments (name) VALUES
    ('OPERATIONS'), ('SALES'), ('FINANCE AND ACCOUNTS'), ('INFORMATION TECHNOLOGY'),
    ('HUMAN RESOURCES');
INSERT INTO __CORE__.branches (name, code) VALUES
    ('AJMER', 1), ('ALWAR', 2), ('BHIWADI', 3), ('JAIPUR HO', 4), ('PUNE', 5);
INSERT INTO __CORE__.partners (type, name) VALUES
    ('Vendor', 'Skyline Travels Pvt Ltd'), ('Vendor', 'Comfort Inn Hotels'),
    ('Vendor', 'Paperworks Stationers'), ('Contractor', 'Netlink Broadband');
INSERT INTO __CORE__.assignees (type, name, employee_code, department_id, email) VALUES
    ('employee', 'Sneha Kulkarni', 'EMP1042', 1, 'sneha@example.com'),
    ('employee', 'Arjun Singh', 'EMP1108', 2, 'arjun@example.com'),
    ('employee', 'Rahul Mehta', 'EMP1201', 1, 'rahul@example.com');
INSERT INTO __CORE__.assignees (type, name, company_name) VALUES
    ('partner', 'Site Supervisor', 'Netlink Broadband');
