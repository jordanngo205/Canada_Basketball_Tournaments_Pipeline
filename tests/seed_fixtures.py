"""Load the committed game fixtures into raw.raw_games.

CI runs dbt against these rather than against a live scrape, for two reasons:
a push should not fail because fiba.basketball is having a slow morning, and
CI should not hit their site on every commit. The live-network check lives in
tests/test_parser.py and runs on a schedule instead.

The fixtures are real payloads captured from three games across two
tournaments, including one that went to overtime, so the period logic gets
exercised rather than assumed.

    python3 -m tests.seed_fixtures
"""

from __future__ import annotations

import gzip
import json
import sys
from datetime import datetime
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingest.load import dsn  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def load() -> int:
    files = sorted(FIXTURES.glob("*.json.gz"))
    if not files:
        raise SystemExit(f"No fixtures found in {FIXTURES}")

    with psycopg.connect(dsn()) as conn:
        with conn.cursor() as cur:
            for path in files:
                with gzip.open(path, "rt", encoding="utf-8") as fh:
                    rec = json.load(fh)
                cur.execute(
                    """
                    INSERT INTO raw.raw_games
                        (game_id, source_url, fetched_at, payload)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (game_id, fetched_at) DO NOTHING
                    """,
                    (
                        rec["game_id"],
                        rec["source_url"],
                        datetime.fromisoformat(rec["fetched_at"]),
                        Jsonb(rec["payload"]),
                    ),
                )
                print(f"seeded {rec['game_id']} from {path.name}")
        conn.commit()
    return len(files)


if __name__ == "__main__":
    print(f"{load()} fixture game(s) loaded")
