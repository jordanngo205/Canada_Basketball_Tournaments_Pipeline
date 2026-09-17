"""Scheduled ingest → transform → test → report for one FIBA event.

The DAG is deliberately thin. Every task calls into a module that runs
perfectly well on its own from the command line, which keeps the pipeline
debuggable without Airflow in the loop and keeps Airflow doing the one job it
is good at: scheduling, retries and logging.

Retry policy is split on purpose. Ingest retries, because FIBA drops
connections under tournament load and that is a flake. Transform and test do
not retry, because a failing dbt model is a bug and running it three more
times only delays finding out.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

from airflow.decorators import dag, task
from airflow.exceptions import AirflowFailException, AirflowSkipException
from airflow.operators.bash import BashOperator

log = logging.getLogger(__name__)

# Which tournament this DAG follows. Override without editing code:
#   airflow variables set fiba_event_slug <slug>
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
    max_active_runs=1,  # two concurrent runs would fight over the raw table
    default_args=DEFAULT_ARGS,
    tags=["fiba", "scouting"],
)
def fiba_scouting_report():
    @task(retries=3, retry_delay=timedelta(minutes=2), retry_exponential_backoff=True)
    def discover_games() -> list[str]:
        """Ask FIBA which games in this event have final stats."""
        from ingest.discover import game_urls

        slug = os.environ.get("FIBA_EVENT_SLUG", DEFAULT_EVENT)
        urls = game_urls(slug, played_only=True)
        if not urls:
            # Nothing played yet is a normal state mid-tournament, not a
            # failure — skip the run rather than page someone at 6am.
            raise AirflowSkipException(f"No completed games yet for {slug}")
        log.info("discovered %d completed game(s) for %s", len(urls), slug)
        return urls

    @task(retries=3, retry_delay=timedelta(minutes=2))
    def ingest_raw(urls: list[str]) -> dict:
        """Land each game's payload in raw.raw_games, untouched."""
        from ingest.load import load_games

        result = load_games(urls)
        log.info("ingest: %s", result)

        # A handful of bad pages is tolerable; a majority failing means FIBA
        # changed something structural and the transforms below would build on
        # a partial event.
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

    # dbt build runs models and their tests together, so a model whose output
    # fails a test never becomes the input to the model below it.
    transform_and_test = BashOperator(
        task_id="dbt_build",
        bash_command=f"cd {DBT_DIR} && dbt build --profiles-dir {DBT_DIR}",
        retries=0,
    )

    @task(retries=1)
    def build_report() -> str:
        """Render the scouting PDF from the tested mart tables."""
        from reports.build_report import build_latest

        path = build_latest()
        log.info("wrote %s", path)
        return path

    urls = discover_games()
    ingested = ingest_raw(urls)
    ingested >> transform_and_test >> build_report()


fiba_scouting_report()
