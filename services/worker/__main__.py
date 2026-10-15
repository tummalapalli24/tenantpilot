"""Background worker: `python -m services.worker`.

Uses RQ's forking Worker on Linux (each job in a fresh child process - a crash or leak
can't take the worker down) and SimpleWorker on macOS, where fork() after loading
Objective-C frameworks is unsafe.
"""

import logging
import sys

from prometheus_client import start_http_server
from redis import Redis
from rq import SimpleWorker, Worker

from packages.core.config import get_settings
from packages.core.logging import configure_logging
from packages.core.queue import INGEST_QUEUE
from packages.core.telemetry import setup_telemetry


def main() -> None:
    settings = get_settings()
    configure_logging("tenantpilot-worker", settings.log_level, settings.log_format)
    setup_telemetry("tenantpilot-worker")
    start_http_server(9102)  # /metrics for Prometheus
    worker_cls = SimpleWorker if sys.platform == "darwin" else Worker
    connection = Redis.from_url(settings.redis_url)
    logging.getLogger(__name__).info("worker starting", extra={"queues": [INGEST_QUEUE]})
    worker_cls([INGEST_QUEUE], connection=connection).work(with_scheduler=True)


if __name__ == "__main__":
    main()
