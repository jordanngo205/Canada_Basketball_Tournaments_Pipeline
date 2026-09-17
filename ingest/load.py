"""Land parsed games in the raw warehouse table.

The only writes in this module are inserts of whole payloads. It does not
reshape the JSON, and it must never start doing so — the moment cleaning
happens here, the raw layer stops being a faithful record of what FIBA served
and the pipeline loses its ability to replay.
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
    """True when this payload differs from the newest one already stored.

    Re-fetching a game that has not changed is normal — a schedule sweeps the
    whole tournament — and storing a byte-identical copy every run would bloat
    the table without adding history worth keeping.
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
    # Compare canonical JSON so key ordering differences don't read as changes.
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
    """Scrape each URL and land any changed payload. Returns a run summary.

    One failed game does not fail the batch: a tournament sweep should land the
    fourteen games it could read rather than lose all fifteen to one bad page.
    The count of failures is returned so the caller can decide.
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
