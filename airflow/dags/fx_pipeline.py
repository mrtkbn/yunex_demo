"""FX pipeline: ingest -> transform -> streamlit. Triggered manually (schedule is commented out).

The pipeline code lives in src/yunex_demo/demo_1 and is mounted into the containers (see
docker-compose.override.yml). MinIO is reached as minio:9000 over the shared fx_net network.
"""

import sys
from datetime import date, timedelta

import pendulum
from airflow.sdk import dag, task

CODE_DIR = "/usr/local/airflow/include/demo_1"
GOLD_TABLES = [
    "dim_date",
    "dim_currency",
    "dim_provider",
    "dim_risk_level",
    "fact_fx_rate",
    "fact_fx_metrics",
    "fact_rate_gap",
]


def _store():
    # imported lazily so DAG parsing stays light and the mount is only needed at run time
    if CODE_DIR not in sys.path:
        sys.path.insert(0, CODE_DIR)
    from storage import Store

    return Store(local=False)


@dag(
    dag_id="fx_pipeline",
    schedule=None,  # manual runs only; to run on a schedule use the line below
    # schedule="0 17 * * 1-5",  # weekdays 17:00 UTC, after the ECB publishes (~16:00 CET)
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    default_args={"retries": 3, "retry_delay": timedelta(minutes=5)},
    params={"lookback_days": 548},  # rolling window, about 18 months
    tags=["fx", "demo_1"],
    doc_md=__doc__,
)
def fx_pipeline():
    @task
    def ingest(params=None) -> dict:
        """ECB + CBAR rates for the rolling window -> MinIO raw/."""
        sys.path.insert(0, CODE_DIR)
        from ingest import run_ingest

        end = date.today()
        start = end - timedelta(days=int(params["lookback_days"]))
        window = {"start": start.isoformat(), "end": end.isoformat()}
        run_ingest(_store(), window["start"], window["end"])
        return window

    @task
    def transform(window: dict) -> None:
        """raw -> silver -> gold star schema for the same window."""
        sys.path.insert(0, CODE_DIR)
        from transform import run_transform

        run_transform(_store(), window["start"], window["end"])

    @task
    def streamlit() -> None:
        """Check that every gold table is readable, then ping the Streamlit app.

        Streamlit is a long-running server, not a job: it re-reads gold when its cache
        expires (5 minutes), so a down app only logs a warning.
        """
        import os
        import urllib.request

        store = _store()
        for table in GOLD_TABLES:
            rows = len(store.get_parquet("gold", f"{table}.parquet"))
            if rows == 0:
                raise ValueError(f"gold/{table}.parquet is empty")
            print(f"gold/{table}.parquet: {rows} rows")

        url = os.getenv("STREAMLIT_URL", "http://host.docker.internal:8501").rstrip("/")
        try:
            with urllib.request.urlopen(f"{url}/_stcore/health", timeout=5) as r:
                print(f"Streamlit is up ({url}): {r.status}")
        except OSError:
            print("Streamlit is not running. Data is ready and will show up when Streamlit starts.")

    transform(ingest()) >> streamlit()


fx_pipeline()
