"""Daily run across every tournament Canada is in.

Kept thin on purpose. Every task is a one-line call into a module that works
fine from a terminal on its own, so you can debug the pipeline without Airflow
in the way. Airflow does scheduling, retries and logging. That's it.

The ingest task is mapped, not looped. `.expand()` makes Airflow create one
task instance per tournament, which means a failed U18 AmeriCup scrape leaves
the World Cup one green and you re-run just the broken square. A for-loop
inside one task would take the whole thing down together and give you nothing
to click on.

Note the retry split further down — ingest retries, dbt doesn't. Deliberate.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from airflow.decorators import dag, task
from airflow.exceptions import AirflowFailException, AirflowSkipException
from airflow.operators.bash import BashOperator

log = logging.getLogger(__name__)

DBT_DIR = "/opt/airflow/dbt"

DEFAULT_ARGS = {
    "owner": "jordan",
    "depends_on_past": False,
    "email_on_failure": False,
}


@dag(
    dag_id="fiba_scouting_report",
    description="Ingest every Canada tournament, transform to KPIs, test, export",
    start_date=datetime(2026, 8, 1),
    schedule="0 6 * * *",
    catchup=False,
    max_active_runs=1,  # two at once would fight over raw_games
    default_args=DEFAULT_ARGS,
    tags=["fiba", "scouting"],
)
def fiba_scouting_report():
    @task(retries=2, retry_delay=timedelta(minutes=2))
    def select_events() -> list[str]:
        """Which tournaments to ingest — pinned plus discovered.

        See config/events.yml. Discovery costs one fetch per candidate event,
        which is why it runs once here rather than inside the mapped task.
        """
        from ingest.discover import select_events as pick

        slugs = pick()
        if not slugs:
            raise AirflowFailException("No events selected — check config/events.yml")
        log.info("ingesting %d event(s): %s", len(slugs), ", ".join(slugs))
        return slugs

    @task(retries=3, retry_delay=timedelta(minutes=2), retry_exponential_backoff=True)
    def ingest_event(slug: str) -> dict:
        """Scrape one tournament and store its payloads untouched.

        Runs once per event via .expand(). Airflow shows each as its own square.
        """
        from ingest.discover import game_urls
        from ingest.load import load_games

        urls = game_urls(slug, played_only=True)
        if not urls:
            # Normal mid-tournament, or for one that hasn't tipped off. Skipping
            # marks the square pink rather than red, which is the honest signal.
            raise AirflowSkipException(f"No completed games yet for {slug}")

        result = load_games(urls)
        log.info("%s — %s", slug, result)

        # A couple of bad pages happens. Most of them failing means FIBA changed
        # something, and everything downstream would be built on half an event.
        if result.failed and result.failed > len(urls) // 2:
            raise AirflowFailException(
                f"{slug}: {result.failed}/{len(urls)} games failed to parse — "
                "likely a FIBA page change, check ingest/fiba.py"
            )

        return {
            "slug": slug,
            "games": len(urls),
            "inserted": result.inserted,
            "skipped": result.skipped,
            "failed": result.failed,
        }

    @task
    def summarise(results: list[dict]) -> dict:
        """Roll the mapped results into one line worth reading in the log.

        Also the join point: dbt should run once, after every event has landed,
        not once per event.
        """
        landed = [r for r in results if r]
        total = {
            "events": len(landed),
            "games": sum(r["games"] for r in landed),
            "inserted": sum(r["inserted"] for r in landed),
            "failed": sum(r["failed"] for r in landed),
        }
        log.info("ingest complete: %s", total)
        return total

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

    slugs = select_events()
    ingested = ingest_event.expand(slug=slugs)
    summarise(ingested) >> transform_and_test >> export_for_dashboard()


fiba_scouting_report()
