"""Dump the mart tables to JSON for the dashboard.

The dashboard is a static page on GitHub Pages — it can't reach Postgres, so
it needs files.

Marts only. Let the page read staging or raw and you've coupled the front end
to FIBA's payload shape, which means every change on their side becomes a
front-end change. The marts are the contract.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import psycopg

log = logging.getLogger(__name__)

DEFAULT_DSN = "postgresql://fiba:fiba@localhost:5433/warehouse"

# Add a mart here and it ships on the next run.
EXPORTS = {
    "analytics_marts.mart_standings": "standings.json",
    "analytics_marts.mart_player_leaders": "player_leaders.json",
    "analytics_marts.mart_team_efficiency": "team_efficiency.json",
    "analytics_staging.stg_games": "games.json",
    "analytics_staging.stg_period_scores": "period_scores.json",
    "analytics_marts.mart_four_factors": "four_factors.json",
    "analytics_marts.mart_shot_zones": "shot_zones.json",
    "analytics_marts.mart_shot_grid": "shot_grid.json",
    "analytics_marts.mart_player_shot_zones": "player_shot_zones.json",
}


def dsn() -> str:
    return os.environ.get("WAREHOUSE_DSN", DEFAULT_DSN)


def out_dir() -> Path:
    return Path(os.environ.get("EXPORT_DIR", "out"))


def encode(value):
    """JSON has no dates or decimals. SQL results are full of both."""
    if isinstance(value, Decimal):
        # Already rounded upstream, so float loses nothing and keeps it small.
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"Cannot serialise {type(value).__name__}")


def export_table(conn: psycopg.Connection, table: str, filename: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT * FROM {table}")  # noqa: S608 - table names are ours
        columns = [d.name for d in cur.description]
        rows = [dict(zip(columns, r)) for r in cur.fetchall()]

    path = out_dir() / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(rows, fh, default=encode, ensure_ascii=False, indent=1)
    log.info("wrote %s — %d rows", path, len(rows))
    return len(rows)


def export_all(conn_str: str | None = None) -> dict[str, int]:
    counts: dict[str, int] = {}
    with psycopg.connect(conn_str or dsn()) as conn:
        for table, filename in EXPORTS.items():
            counts[filename] = export_table(conn, table, filename)

    # Lets the page show a "last updated" and makes a stale deploy obvious.
    manifest = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "files": counts,
    }
    (out_dir() / "manifest.json").write_text(
        json.dumps(manifest, indent=1), encoding="utf-8"
    )
    return counts


if __name__ == "__main__":  # pragma: no cover - manual run
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    result = export_all()
    print(f"{sum(result.values())} rows across {len(result)} files -> {out_dir()}/")
