from __future__ import annotations

import logging
import signal
import time

from app.config import get_settings
from app.database import SessionLocal, engine, validate_database_server
from app.execution import ExecutionService, claim_next_job, recover_stale_jobs


def main() -> None:
    settings = get_settings()
    settings.validate_for_server()
    validate_database_server(engine)
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger = logging.getLogger("canvas_merger.worker")
    stopping = False

    def stop(*_: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    service = ExecutionService(settings)
    logger.info("worker_started")
    with SessionLocal() as db:
        recovered = recover_stale_jobs(db)
        if recovered:
            logger.warning("stale_jobs_requeued count=%s", recovered)
    while not stopping:
        with SessionLocal() as db:
            job = claim_next_job(db)
            if job:
                logger.info("job_started job_id=%s group_id=%s", job.id, job.group_id)
                service.run_job(db, job.id)
                logger.info("job_finished job_id=%s", job.id)
            else:
                time.sleep(settings.worker_poll_seconds)
    logger.info("worker_stopped")


if __name__ == "__main__":
    main()
