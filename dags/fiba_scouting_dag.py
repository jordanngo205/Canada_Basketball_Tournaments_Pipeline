"""Daily run for one FIBA event: discover -> ingest -> dbt -> export.

Kept thin on purpose. Every task is a one-line call into a module that works
fine from a terminal on its own, so you can debug the pipeline without
Airflow in the way. Airflow does scheduling, retries and logging. That's it.

Note the retry split below — ingest retries, dbt doesn't. Deliberate.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

from airflow.decorators import dag, task
from airflow.exceptions import AirflowFailException, AirflowSkipException
from airflow.operators.bash import BashOperator

log = logging.getLogger(__name__)

# Override with the FIBA_EVENT_SLUG env var rather than editing this.
DEFAULT_EVENT = "fiba-womens-olympic-pre-qualifying-tournament-2026-guadalajara-mexico"
DBT_DIR = "/opt/airflow/dbt"

DEFAULT_ARGS = {
    "owner": "jordan",
    "depends_on_past": False,
    "email_on_failure": False,
}


@dag(
    dag_id="fiba_scouting_report",
    description="Ingest FIBA games, transform to KPIs, test, and build the scouting PDF",
    start_date=datetime(2026, 8, 1),
    schedule="0 6 * * *",  # daily, after a tournament day's games are final
    catchup=False,
    max_active_runs=1,  # two at once would fight over raw_games
    default_args=DEFAULT_ARGS,
    tags=["fiba", "scouting"],
)
def fiba_scouting_report():
    @task(retries=3, retry_delay=timedelta(minutes=2), retry_exponential_backoff=True)
    def discover_games() -> list[str]:
        """Which games in this event have final stats?"""
        from ingest.discover import game_urls

        slug = os.environ.get("FIBA_EVENT_SLUG", DEFAULT_EVENT)
        urls = game_urls(slug, played_only=True)
        if not urls:
            # Mid-tournament this is normal, not a failure. Skip rather than
            # wake someone up at 6am.
            raise AirflowSkipException(f"No completed games yet for {slug}")
        log.info("discovered %d completed game(s) for %s", len(urls), slug)
        return urls

    @task(retries=3, retry_delay=timedelta(minutes=2))
    def ingest_raw(urls: list[str]) -> dict:
        """Store each game's payload untouched."""
        from ingest.load import load_games

        result = load_games(urls)
        log.info("ingest: %s", result)

        # A couple of bad pages happens. Most of them failing means FIBA
        # changed something, and everything downstream would be built on half
        # a tournament.
        if result.failed and result.failed > len(urls) // 2:
            raise AirflowFailException(
                f"{result.failed}/{len(urls)} games failed to parse — "
                "likely a FIBA page-structure change; check ingest/fiba.py"
            )
        return {
            "inserted": result.inserted,
            "skipped": result.skipped,
            "failed": result.failed,
        }

    # `build`, not `run` then `test` — build interleaves them, so a model that
    # fails its test never feeds the one below it.
    # deps first: a fresh clone has no dbt_packages.
    transform_and_test = BashOperator(
        task_id="dbt_build",
        bash_command=(
            f"cd {DBT_DIR} && "
            f"dbt deps --profiles-dir {DBT_DIR} && "
            f"dbt build --profiles-dir {DBT_DIR}"
        ),
        # No retries. A failing test is a bug, and running it three more times
        # just delays finding out. Ingest retries because dropped connections
        # are a flake; this isn't.
        retries=0,
    )

    @task(retries=1)
    def export_for_dashboard() -> dict:
        """Marts -> JSON files for the dashboard."""
        from reports.export_marts import export_all

        counts = export_all()
        log.info("exported %s", counts)
        return counts

    urls = discover_games()
    ingested = ingest_raw(urls)
    ingested >> transform_and_test >> export_for_dashboard()


fiba_scouting_report()
