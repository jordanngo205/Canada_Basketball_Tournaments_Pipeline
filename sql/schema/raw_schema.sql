-- The raw layer, applied to whichever database psql is pointed at.
--
-- Deliberately contains no \connect. A schema file that overrides the
-- connection it was handed is a trap: an earlier version of this carried one,
-- and when it was first tested against a throwaway database it silently wrote
-- to the wrong one instead. Callers choose the target — sql/init applies it to
-- the warehouse on first boot, CI applies it to $WAREHOUSE_DSN.
--
-- Safe to re-run: every statement is IF NOT EXISTS or OR REPLACE.

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

-- Exactly one row per game: the most recent fetch. dbt sources read this rather
-- than raw_games, so re-ingesting a game to pick up a correction replaces it
-- downstream instead of duplicating it.
CREATE OR REPLACE VIEW raw.latest_games AS
SELECT DISTINCT ON (game_id)
       game_id, source_url, fetched_at, payload
FROM   raw.raw_games
ORDER  BY game_id, fetched_at DESC;

CREATE SCHEMA IF NOT EXISTS analytics;
