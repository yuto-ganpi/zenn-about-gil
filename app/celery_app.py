import importlib
import os

from celery import Celery

BROKER_URL = os.getenv("CELERY_BROKER_URL", "amqp://guest:guest@rabbitmq:5672//")
RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "rpc://")

app = Celery("gil_demo", broker=BROKER_URL, backend=RESULT_BACKEND)
app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_acks_late=False,
    worker_prefetch_multiplier=1,
)

# Ensure tasks are registered when the worker starts.
importlib.import_module("tasks")
