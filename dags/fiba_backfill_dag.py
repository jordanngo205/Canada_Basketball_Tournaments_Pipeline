"""Backfill: re-ingest named tournaments, or rebuild downstream from raw.

Not a date-partitioned catchup, and that's deliberate. The textbook Airflow
backfill replays a date range, which assumes the source can tell you what it
looked like on that date. FIBA can't — a game page serves the current box
score and nothing else. Asking for "17 August as it stood on the 18th" is not
a question fiba.basketball can answer, so a date-partitioned DAG here would be
pure theatre.

What's actually useful is two things, and this DAG does both:

  1. `rebuild_only: true` (the default) — re-run every dbt model and test
     against the raw payloads already stored, then re-export. No network at
     all. This is the one that gets used: it's how a corrected formula or a new
     model reaches the marts, and it takes seconds rather than minutes.

  2. `rebuild_only: false` with `events` — re-scrape those tournaments first.
     For when FIBA has corrected a box score after the fact, or a tournament
     was ingested while the parser had a bug in it.

Trigger it from the UI with a config like:

    {"rebuild_only": false,
     "events": ["fiba-u18-womens-americup-2026"]}

or leave the config empty for a plain rebuild of everything.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from airflow.decorators import dag, task
from airflow.exceptions import AirflowFailException, AirflowSkipException
from airflow.models.param import Param
from airflow.operators.bash import BashOperator

log = logging.getLogger(__name__)

DBT_DIR = "/opt/airflow/dbt"


@dag(
    dag_id="fiba_backfill",
    description="Re-ingest named tournaments, or rebuild the marts from stored raw",
    start_date=datetime(2026, 8, 1),
    schedule=None,  # manual only — there is nothing to schedule
    catchup=False,
    max_active_runs=1,  # must not race the daily DAG over raw_games
    default_args={"owner": "jordan", "email_on_failure": False},
    tags=["fiba", "backfill", "manual"],
    params={
        "rebuild_only": Param(
            True,
            type="boolean",
            description="Rebuild models from stored raw without touching FIBA.",
        ),
        "events": Param(
            [],
            type="array",
            description="Event slugs to re-scrape. Empty means everything in config/events.yml.",
        ),
        "full_refresh": Param(
            False,
            type="boolean",
            description="Drop and rebuild incremental models rather than updating them.",
        ),
    },
)
def fiba_backfill():
    @task
    def resolve_events(**context) -> list[str]:
        """Work out which tournaments to re-scrape, if any."""
        params = context["params"]
        if params["rebuild_only"]:
            # Skipping here marks the task pink and lets dbt run, which is the
            # whole point of a rebuild.
            raise AirflowSkipException("rebuild_only — skipping ingest entirely")

        slugs = params["events"]
        if not slugs:
            from ingest.discover import select_events as pick
            slugs = pick()
            log.info("no events given, falling back to config: %d found", len(slugs))

        if not slugs:
            raise AirflowFailException("Nothing to re-scrape and rebuild_only is false")
        log.info("re-scraping %d event(s): %s", len(slugs), ", ".join(slugs))
        return slugs

    @task(retries=3, retry_delay=timedelta(minutes=2), retry_exponential_backoff=True)
    def reingest(slug: str) -> dict:
        """Re-scrape one tournament.

        Nothing is deleted first. raw_games is keyed on (game_id, fetched_at),
        so a corrected box score lands as a new row beside the old one and
        raw.latest_games picks the newer. The previous version stays on disk,
        which is the point of an append-only raw layer — a bad re-scrape is
        recoverable.
        """
        from ingest.discover import event_fixtures
        from ingest.load import load_games, store_results

        urls, results = event_fixtures(slug)
        store_results(slug, results)
        if not urls:
            raise AirflowSkipException(f"No completed games for {slug}")
        result = load_games(urls)
        log.info("%s — %s", slug, result)
        return {"slug": slug, "games": len(urls), "inserted": result.inserted}

    # trigger_rule so dbt still runs when the ingest above was skipped — which
    # is exactly what happens on a rebuild_only run, the common case.
    rebuild = BashOperator(
        task_id="dbt_build",
        bash_command=(
            f"cd {DBT_DIR} && dbt deps --profiles-dir {DBT_DIR} && "
            "dbt build --profiles-dir " + DBT_DIR +
            "{% if params.full_refresh %} --full-refresh{% endif %}"
        ),
        trigger_rule="none_failed",
        retries=0,
    )

    @task(trigger_rule="none_failed")
    def export() -> dict:
        from reports.export_marts import export_all

        counts = export_all()
        log.info("exported %s", counts)
        return counts

    reingest.expand(slug=resolve_events()) >> rebuild >> export()


fiba_backfill()
