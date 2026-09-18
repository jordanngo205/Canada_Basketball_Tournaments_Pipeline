#!/bin/bash
# Postgres' init runner executes this attached to POSTGRES_DB (airflow), so the
# schema has to be applied to the warehouse explicitly. CI applies the same
# file directly with psql instead of going through here.
set -e
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d warehouse -f /opt/sql/raw_schema.sql
