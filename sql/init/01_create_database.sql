-- Runs once, on first boot of an empty Postgres volume.
--
-- The warehouse is a separate database from Airflow's metadata so a `dbt run`
-- can never contend with, or corrupt, the scheduler's own state.
--
-- CI skips this file: the Postgres service container is started with
-- POSTGRES_DB=warehouse, so the database already exists there.
CREATE DATABASE warehouse;
