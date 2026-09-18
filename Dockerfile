FROM apache/airflow:2.10.3-python3.11

USER root
# psycopg builds against libpq; the slim Airflow image ships without headers.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libpq-dev \
 && rm -rf /var/lib/apt/lists/*

USER airflow
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# The project is COPIED into the image rather than bind-mounted from the host.
#
# This is how Airflow is deployed in practice — DAGs and their dependencies
# ship as part of the image rather than syncing from someone's laptop — and it
# makes the image self-contained and reproducible.
#
# Worth knowing if this project is ever cloned onto another Mac: macOS gates
# ~/Documents behind a privacy permission, and without it Docker can stat the
# files (correct names and sizes) but every read fails with
# "OSError: [Errno 5] Input/output error". Both bind mounts and the build
# context hit it, so the symptom is unreadable source in a container that
# looks correctly populated. The fix is System Settings -> Privacy & Security
# -> Files and Folders -> Docker -> Documents Folder.
#
# The cost of COPY is that code changes need `docker compose build` before
# they take effect. `out/` stays a bind mount because the host needs the JSON.
COPY --chown=airflow:root dags/   /opt/airflow/dags/
COPY --chown=airflow:root ingest/ /opt/airflow/ingest/
COPY --chown=airflow:root dbt/    /opt/airflow/dbt/
COPY --chown=airflow:root reports/ /opt/airflow/reports/
COPY --chown=airflow:root tests/  /opt/airflow/tests/
COPY --chown=airflow:root config/ /opt/airflow/config/

# Vendor the dbt packages at build time so the DAG never needs network for it.
RUN cd /opt/airflow/dbt && dbt deps --profiles-dir . || true

ENV PYTHONPATH=/opt/airflow
