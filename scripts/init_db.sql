-- Shekel Budget App — Database Schema Initialization
-- Creates the PostgreSQL schemas required by the application.
-- Safe to run multiple times (IF NOT EXISTS).

CREATE SCHEMA IF NOT EXISTS ref;
CREATE SCHEMA IF NOT EXISTS auth;
CREATE SCHEMA IF NOT EXISTS budget;
CREATE SCHEMA IF NOT EXISTS salary;
CREATE SCHEMA IF NOT EXISTS system;
