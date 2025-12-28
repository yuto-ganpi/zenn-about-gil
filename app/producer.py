import os
import time

from celery_app import app

ITERATIONS = int(os.getenv("ITERATIONS", "10"))
ROWS = int(os.getenv("DUCKDB_ROWS", "500000000"))
TIMEOUT_SEC = float(os.getenv("DUCKDB_TIMEOUT", "2"))
TASK_NAME = os.getenv("TASK_NAME", "tasks.leak_demo")
TARGET_QUEUES = [
    q.strip()
    for q in os.getenv("TARGET_QUEUES", "gil,nogil").split(",")
    if q.strip()
]


def _send(queue: str) -> None:
    result = app.send_task(
        TASK_NAME,
        args=[ITERATIONS, ROWS, TIMEOUT_SEC],
        queue=queue,
    )
    print(f"sent queue={queue} task_id={result.id}")


if __name__ == "__main__":
    for queue in TARGET_QUEUES:
        _send(queue)
        time.sleep(1)
