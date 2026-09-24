"""Write parsed games into raw.raw_games.

Whole payloads, inserted as-is. Don't add reshaping here, however tempting —
the moment this starts cleaning things, raw stops being a faithful copy of
what FIBA sent and we lose the ability to rebuild from it.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

import psycopg
from psycopg.types.json import Jsonb

from ingest.fiba import GameNotFound, RawGame, scrape_game

log = logging.getLogger(__name__)

DEFAULT_DSN = "postgresql://fiba:fiba@localhost:5433/warehouse"


def dsn() -> str:
    return os.environ.get("WAREHOUSE_DSN", DEFAULT_DSN)


@dataclass
class LoadResult:
    inserted: int = 0
    skipped: int = 0
    failed: int = 0

    def __str__(self) -> str:
        return (
            f"{self.inserted} inserted, {self.skipped} unchanged, "
            f"{self.failed} failed"
        )


def payload_changed(conn: psycopg.Connection, raw: RawGame) -> bool:
    """Has anything changed since the last time we stored this game?

    A scheduled sweep re-fetches the whole tournament every run, so most games
    come back identical. No point storing another copy of the same bytes.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT payload
            FROM   raw.raw_games
            WHERE  game_id = %s
            ORDER  BY fetched_at DESC
            LIMIT  1
            """,
            (raw.game_id,),
        )
        row = cur.fetchone()
    if row is None:
        return True
    # sort_keys so a reordered dict doesn't look like a change.
    return json.dumps(row[0], sort_keys=True) != json.dumps(
        raw.payload, sort_keys=True
    )


def insert(conn: psycopg.Connection, raw: RawGame) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw.raw_games (game_id, source_url, fetched_at, payload)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (game_id, fetched_at) DO NOTHING
            """,
            (raw.game_id, raw.source_url, raw.fetched_at, Jsonb(raw.payload)),
        )


def load_games(urls: list[str], conn_str: str | None = None) -> LoadResult:
    """Scrape each URL, store anything that changed.

    One bad page shouldn't cost you the other fourteen games, so failures are
    counted and returned rather than raised. Caller decides what's too many.
    """
    result = LoadResult()
    with psycopg.connect(conn_str or dsn()) as conn:
        for url in urls:
            try:
                raw = scrape_game(url)
            except (GameNotFound, RuntimeError) as exc:
                log.warning("skipping %s — %s: %s", url, type(exc).__name__, exc)
                result.failed += 1
                continue

            if raw.warnings:
                log.warning("game %s: %s", raw.game_id, ", ".join(raw.warnings))

            if payload_changed(conn, raw):
                insert(conn, raw)
                result.inserted += 1
                log.info("stored %s — %s", raw.game_id, raw.summary())
            else:
                result.skipped += 1
        conn.commit()
    return result


if __name__ == "__main__":  # pragma: no cover - manual run
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(load_games(sys.argv[1:]))


def store_results(slug: str, results: list[dict], conn_str: str | None = None) -> int:
    """Store games that have a final result but no box score (forfeits).

    Upserted rather than appended: there is no payload to keep a history of,
    only a score, and a corrected score should simply replace the old one.
    """
    if not results:
        return 0
    with psycopg.connect(conn_str or dsn()) as conn:
        with conn.cursor() as cur:
            for r in results:
                cur.execute(
                    """
                    INSERT INTO raw.result_only_games
                        (game_id, event_slug, round_name, game_date,
                         home_code, away_code, home_score, away_score)
                    VALUES (%s, %s, %s, nullif(%s, '')::date, %s, %s, %s, %s)
                    ON CONFLICT (game_id) DO UPDATE SET
                        event_slug = excluded.event_slug,
                        round_name = excluded.round_name,
                        game_date  = excluded.game_date,
                        home_code  = excluded.home_code,
                        away_code  = excluded.away_code,
                        home_score = excluded.home_score,
                        away_score = excluded.away_score,
                        fetched_at = now()
                    """,
                    (r["game_id"], slug, r["round"], r["date"], r["home"], r["away"],
                     r["home_score"], r["away_score"]),
                )
        conn.commit()
    log.info("%s: stored %d result-only game(s)", slug, len(results))
    return len(results)
