FROM apache/airflow:2.10.3-python3.11

USER root
# psycopg builds against libpq; the slim Airflow image ships without headers.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libpq-dev \
 && rm -rf /var/lib/apt/lists/*

USER airflow
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Modules are bind-mounted at runtime; this makes them importable from tasks.
ENV PYTHONPATH=/opt/airflow
