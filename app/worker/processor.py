
import concurrent.futures
import logging
import signal
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, or_

from app.core.config import settings
from app.db.models import OutboxJob, Transaction
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = settings.MAX_ATTEMPTS
RECOVERY_TIMEOUT_SECONDS = settings.RECOVERY_TIMEOUT_SECONDS
POLL_INTERVAL_SECONDS = settings.POLL_INTERVAL_SECONDS
WORKER_CONCURRENCY = settings.WORKER_CONCURRENCY
PROCESSING_DELAY_SECONDS = settings.PROCESSING_DELAY_SECONDS
RECOVERY_INTERVAL_SECONDS = settings.RECOVERY_INTERVAL_SECONDS


def recover_stale_jobs(timeout_seconds: int | None = None) -> int:
    """Return abandoned PROCESSING jobs to PENDING or mark them FAILED."""
    timeout = (
        timeout_seconds
        if timeout_seconds is not None
        else settings.RECOVERY_TIMEOUT_SECONDS
    )
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=timeout)
    recovered_count = 0

    with SessionLocal() as db:
        with db.begin():
            jobs = db.execute(
                select(OutboxJob)
                .where(
                    OutboxJob.status == "PROCESSING",
                    or_(
                        OutboxJob.locked_at.is_(None),
                        OutboxJob.locked_at < cutoff,
                    ),
                )
                .with_for_update(skip_locked=True)
            ).scalars().all()

            for job in jobs:
                job.locked_at = None
                job.last_error = "Recovered stale processing job"

                if job.attempts >= settings.MAX_ATTEMPTS:
                    job.status = "FAILED"
                    transaction = db.execute(
                        select(Transaction).where(
                            Transaction.transaction_id == job.transaction_id
                        )
                    ).scalar_one_or_none()

                    if transaction is not None:
                        transaction.status = "FAILED"
                else:
                    job.status = "PENDING"

                recovered_count += 1

    if recovered_count:
        logger.warning("Recovered %s stale job(s)", recovered_count)

    return recovered_count


def claim_one_job() -> int | None:
    """Atomically claim one pending job and return its ID."""
    with SessionLocal() as db:
        with db.begin():
            job = db.execute(
                select(OutboxJob)
                .where(OutboxJob.status == "PENDING")
                .order_by(OutboxJob.created_at, OutboxJob.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            ).scalar_one_or_none()

            if job is None:
                return None

            job.status = "PROCESSING"
            job.locked_at = datetime.now(timezone.utc)
            job.attempts += 1
            job_id = job.id

    return job_id


def process_job(job_id: int, delay_seconds: float | None = None) -> None:
    """Simulate an external operation and persist its result."""
    delay = (
        delay_seconds
        if delay_seconds is not None
        else settings.PROCESSING_DELAY_SECONDS
    )
    try:
        if delay > 0:
            time.sleep(delay)

        with SessionLocal() as db:
            with db.begin():
                job = db.execute(
                    select(OutboxJob)
                    .where(OutboxJob.id == job_id)
                    .with_for_update()
                ).scalar_one_or_none()

                if job is None or job.status != "PROCESSING":
                    logger.warning("Job %s is no longer processing", job_id)
                    return

                transaction = db.execute(
                    select(Transaction)
                    .where(
                        Transaction.transaction_id == job.transaction_id
                    )
                    .with_for_update()
                ).scalar_one()

                transaction.status = "PROCESSED"
                transaction.processed_at = datetime.now(timezone.utc)

                job.status = "COMPLETED"
                job.locked_at = None
                job.last_error = None

        logger.info("Successfully processed job %s", job_id)

    except Exception as exc:
        logger.exception("Failed to process job %s", job_id)

        # Persist the failure in a fresh database session.
        try:
            with SessionLocal() as db:
                with db.begin():
                    job = db.execute(
                        select(OutboxJob)
                        .where(OutboxJob.id == job_id)
                        .with_for_update()
                    ).scalar_one_or_none()

                    if job is None or job.status != "PROCESSING":
                        return

                    job.locked_at = None
                    job.last_error = str(exc)[:2000]

                    if job.attempts >= settings.MAX_ATTEMPTS:
                        job.status = "FAILED"

                        transaction = db.execute(
                            select(Transaction).where(
                                Transaction.transaction_id
                                == job.transaction_id
                            ).with_for_update()
                        ).scalar_one_or_none()

                        if transaction is not None:
                            transaction.status = "FAILED"
                    else:
                        job.status = "PENDING"

        except Exception:
            logger.exception(
                "Could not persist failure state for job %s", job_id
            )


def run_worker(
    max_workers: int | None = None,
    poll_interval: float | None = None,
    stop_event: threading.Event | None = None,
) -> None:
    """Continuously claim and process jobs concurrently using an in-process thread pool."""
    concurrency = (
        max_workers
        if max_workers is not None
        else settings.WORKER_CONCURRENCY
    )
    interval = (
        poll_interval
        if poll_interval is not None
        else settings.POLL_INTERVAL_SECONDS
    )

    if stop_event is None:
        stop_event = threading.Event()
        if threading.current_thread() is threading.main_thread():
            def _handle_shutdown(signum, _frame):
                logger.info(
                    "Shutdown signal received (%s). Stopping worker gracefully...",
                    signum,
                )
                stop_event.set()

            signal.signal(signal.SIGINT, _handle_shutdown)
            signal.signal(signal.SIGTERM, _handle_shutdown)

    logger.info(
        "Transaction worker started with thread pool (concurrency=%d, poll_interval=%.1fs)",
        concurrency,
        interval,
    )

    active_futures: set[concurrent.futures.Future] = set()
    last_recovery_time = 0.0

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=concurrency,
        thread_name_prefix="job-worker",
    ) as executor:
        while not stop_event.is_set():
            try:
                # 1. Clean up completed futures
                done_futures = {f for f in active_futures if f.done()}
                active_futures.difference_update(done_futures)
                for f in done_futures:
                    exc = f.exception()
                    if exc:
                        logger.error(
                            "Job worker thread raised an unexpected exception: %s",
                            exc,
                        )

                # 2. Periodically recover stale jobs
                now = time.monotonic()
                if now - last_recovery_time >= settings.RECOVERY_INTERVAL_SECONDS:
                    try:
                        recover_stale_jobs()
                    except Exception:
                        logger.exception("Error during stale job recovery")
                    last_recovery_time = now

                # 3. Claim and dispatch jobs as long as worker capacity is available
                while len(active_futures) < concurrency and not stop_event.is_set():
                    try:
                        job_id = claim_one_job()
                    except Exception:
                        logger.exception("Error claiming job from database")
                        break

                    if job_id is None:
                        break

                    logger.info(
                        "Claimed job %s (active tasks: %d/%d)",
                        job_id,
                        len(active_futures) + 1,
                        concurrency,
                    )
                    future = executor.submit(process_job, job_id)
                    active_futures.add(future)

                # 4. Wait for running jobs or poll interval before claiming more
                if active_futures:
                    concurrent.futures.wait(
                        active_futures,
                        timeout=interval,
                        return_when=concurrent.futures.FIRST_COMPLETED,
                    )
                else:
                    stop_event.wait(timeout=interval)

            except KeyboardInterrupt:
                logger.info("Worker keyboard interrupt received")
                stop_event.set()
                break

            except Exception:
                logger.exception("Unexpected error in worker loop")
                stop_event.wait(timeout=2.0)

        logger.info(
            "Worker shutdown initiated. Waiting for %d in-flight job(s) to finish...",
            len(active_futures),
        )

    logger.info("Worker shutdown complete. All jobs finished.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    run_worker()
