-- Runs once, on first boot of an empty Postgres volume.
--
-- The warehouse is a separate database from Airflow's metadata so that a
-- `dbt run` can never contend with, or corrupt, the scheduler's own state.
CREATE DATABASE warehouse;

\connect warehouse

-- Raw is append-only and holds FIBA's payload exactly as served. Nothing here
-- is cleaned, renamed or computed: that is dbt's job, downstream. Keeping this
-- layer untouched is what lets a wrong transform be fixed by re-running dbt
-- instead of re-scraping FIBA.
CREATE SCHEMA IF NOT EXISTS raw;

CREATE TABLE IF NOT EXISTS raw.raw_games (
    game_id      text        NOT NULL,
    source_url   text        NOT NULL,
    fetched_at   timestamptz NOT NULL DEFAULT now(),
    payload      jsonb       NOT NULL,
    PRIMARY KEY (game_id, fetched_at)
);

-- The common read pattern is "latest payload for this game", so index the
-- descending fetch time alongside the id.
CREATE INDEX IF NOT EXISTS raw_games_game_id_fetched_idx
    ON raw.raw_games (game_id, fetched_at DESC);

-- A view giving exactly one row per game: the most recent successful fetch.
-- dbt sources read this rather than raw_games, so re-ingesting a game to pick
-- up a correction never double-counts it downstream.
CREATE OR REPLACE VIEW raw.latest_games AS
SELECT DISTINCT ON (game_id)
       game_id, source_url, fetched_at, payload
FROM   raw.raw_games
ORDER  BY game_id, fetched_at DESC;

CREATE SCHEMA IF NOT EXISTS analytics;
