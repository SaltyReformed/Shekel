-- Shekel Budget App — Database Schema Initialization
-- Creates the PostgreSQL schemas required by the application.
-- Safe to run multiple times (IF NOT EXISTS).

CREATE SCHEMA IF NOT EXISTS ref;
CREATE SCHEMA IF NOT EXISTS auth;
CREATE SCHEMA IF NOT EXISTS budget;
CREATE SCHEMA IF NOT EXISTS salary;
CREATE SCHEMA IF NOT EXISTS system;

-- Alembic migration tracking table.
-- Pre-created to avoid transactional DDL issues with include_schemas.
CREATE TABLE IF NOT EXISTS public.alembic_version (
    version_num VARCHAR(32) NOT NULL,
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);
