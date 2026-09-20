"""One unattended run: discover, ingest, transform, test, publish.

This is what the scheduled GitHub Action calls. It is deliberately the same
four steps the Airflow DAG runs, calling the same functions — ingest/ and
reports/ hold the logic, and both orchestrators stay thin. Airflow is the
local, observable version with a square per tournament and per-task retries;
this is the version that runs on a machine nobody owns.

    python -m scripts.run_pipeline --docs docs

Everything comes from one environment variable, WAREHOUSE_DSN, because one
GitHub secret is easier to get right than six. dbt cannot read a DSN — its
profile wants host, port, user, password, dbname and sslmode as separate
values — so the DSN is parsed and exported before dbt is invoked.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

log = logging.getLogger("pipeline")


def dbt_env_from_dsn(dsn: str) -> dict[str, str]:
    """Split a postgres:// URL into the variables dbt/profiles.yml reads.

    Hosted providers put the password in the URL, so it is unquoted here and
    passed through the environment rather than anywhere it could be logged.
    Neon appends ?sslmode=require; that is honoured if present and otherwise
    left to the profile's own default.
    """
    u = urlparse(dsn)
    if u.scheme not in ("postgres", "postgresql"):
        raise SystemExit(f"WAREHOUSE_DSN is not a postgres URL: {u.scheme!r}")

    env = {
        "POSTGRES_HOST": u.hostname or "",
        "POSTGRES_PORT": str(u.port or 5432),
        "POSTGRES_USER": unquote(u.username or ""),
        "POSTGRES_PASSWORD": unquote(u.password or ""),
        "POSTGRES_DB": (u.path or "/warehouse").lstrip("/") or "warehouse",
    }
    for part in (u.query or "").split("&"):
        if part.startswith("sslmode="):
            env["POSTGRES_SSLMODE"] = part.split("=", 1)[1]
    if not env["POSTGRES_HOST"]:
        raise SystemExit("WAREHOUSE_DSN has no host")
    return env


def apply_schema(dsn: str) -> None:
    """Create the raw layer if this database has never been used before.

    Every statement in raw_schema.sql is IF NOT EXISTS or OR REPLACE, so this
    is safe on every run and means a brand-new hosted database needs no manual
    setup step before the first ingest.
    """
    import psycopg

    sql = (ROOT / "sql" / "schema" / "raw_schema.sql").read_text()
    with psycopg.connect(dsn) as conn:
        conn.execute(sql)
        conn.commit()
    log.info("raw schema applied")


def ingest(dsn: str) -> dict:
    """Scrape every selected tournament into the raw layer.

    One tournament failing does not stop the others — the same reasoning as
    the DAG's .expand(): a bad U18 scrape should not cost you the World Cup.
    Unlike the DAG there is no per-tournament retry, because the whole job is
    cheap to re-run and the next scheduled run is an hour away.
    """
    os.environ["WAREHOUSE_DSN"] = dsn
    from ingest.discover import game_urls, select_events
    from ingest.load import load_games

    slugs = select_events()
    if not slugs:
        raise SystemExit("No events selected — check config/events.yml")
    log.info("%d event(s) selected", len(slugs))

    totals = {"events": 0, "games": 0, "inserted": 0, "failed": 0, "errors": []}
    for slug in slugs:
        try:
            urls = game_urls(slug, played_only=True)
        except Exception as exc:  # noqa: BLE001 — one bad event must not stop the sweep
            log.warning("%s — could not list games: %s", slug, exc)
            totals["errors"].append(slug)
            continue

        if not urls:
            log.info("%s — no completed games yet", slug)
            continue

        result = load_games(urls)
        log.info("%s — %s", slug, result)
        totals["events"] += 1
        totals["games"] += len(urls)
        totals["inserted"] += result.inserted
        totals["failed"] += result.failed

        # Same guard as the DAG: a couple of bad pages is life, most of them
        # failing means FIBA changed their markup and everything downstream
        # would be built on half a tournament.
        if result.failed and result.failed > len(urls) // 2:
            raise SystemExit(
                f"{slug}: {result.failed}/{len(urls)} games failed to parse — "
                "likely a FIBA page change, check ingest/fiba.py"
            )

    log.info("ingest complete: %s", totals)
    return totals


def dbt_build(env: dict[str, str]) -> None:
    """dbt deps then dbt build. Build, not run-then-test, so a model that
    fails its test never becomes the input to the one below it."""
    full = {**os.environ, **env}
    for cmd in (["dbt", "deps", "--profiles-dir", "."], ["dbt", "build", "--profiles-dir", "."]):
        log.info("running: %s", " ".join(cmd))
        proc = subprocess.run(cmd, cwd=ROOT / "dbt", env=full)
        if proc.returncode != 0:
            raise SystemExit(f"{cmd[1]} failed with exit code {proc.returncode}")


def publish(dsn: str, docs: str) -> None:
    os.environ["WAREHOUSE_DSN"] = dsn
    from reports.build_dashboard import build_all

    build_all(Path(docs))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--docs", default="docs", help="Output root for the site")
    ap.add_argument("--skip-ingest", action="store_true",
                    help="Rebuild and republish from stored raw, no network")
    args = ap.parse_args()

    dsn = os.environ.get("WAREHOUSE_DSN")
    if not dsn:
        raise SystemExit("WAREHOUSE_DSN is not set")

    env = dbt_env_from_dsn(dsn)
    # Host only — never the DSN itself, which carries the password and would
    # land in a public Actions log.
    log.info("warehouse: %s/%s", env["POSTGRES_HOST"], env["POSTGRES_DB"])

    apply_schema(dsn)
    if not args.skip_ingest:
        ingest(dsn)
    dbt_build(env)
    publish(dsn, args.docs)
    log.info("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
