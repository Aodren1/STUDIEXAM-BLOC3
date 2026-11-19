"""DAG Airflow de production : enchaîne extraction, jobs Spark et publication, une fois par nuit.

Hors périmètre des tests (Airflow n'est pas une dépendance du dépôt) ; il décrit l'ordonnancement cible.
Les tâches appellent exactement les commandes utilisées en recette (python -m fusion, infra/spark/submit-all.sh).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator

DEFAULTS = {
    "owner": "equipe-data",
    "retries": 2,
    "retry_delay": timedelta(minutes=10),
    "email_on_failure": True,
}

with DAG(
    dag_id="fusion_abassurance_assureplus",
    description="Talend/extraction → Kafka → Spark (silver, doublons, gold) → HDFS → tableau de bord",
    schedule="0 2 * * *",
    start_date=datetime(2026, 11, 1),
    catchup=False,
    max_active_runs=1,  # deux exécutions concurrentes rejoueraient les mêmes offsets
    default_args=DEFAULTS,
    tags=["fusion", "big-data"],
) as dag:
    extraction = BashOperator(task_id="extraction_talend_vers_kafka",
                              bash_command="python -m fusion extract")
    spark = BashOperator(task_id="jobs_spark", bash_command="bash infra/spark/submit-all.sh",
                         execution_timeout=timedelta(hours=3))
    # contrôle de réconciliation et publication du gold vers le tableau de bord (copie locale du gold HDFS)
    controle = BashOperator(task_id="reconciliation_et_publication", bash_command="python -m fusion run")
    extraction >> spark >> controle
