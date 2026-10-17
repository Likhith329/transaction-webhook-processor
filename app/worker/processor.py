
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, or_

from app.db.models import OutboxJob, Transaction
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RECOVERY_TIMEOUT_SECONDS = 60
POLL_INTERVAL_SECONDS = 1


def recover_stale_jobs() -> int:
    """Return abandoned PROCESSING jobs to PENDING or mark them FAILED."""
    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=RECOVERY_TIMEOUT_SECONDS
    )
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

                if job.attempts >= MAX_ATTEMPTS:
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


def process_job(job_id: int) -> None:
    """Simulate a 30-second external operation and persist its result."""
    try:
        time.sleep(30)

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

                    if job.attempts >= MAX_ATTEMPTS:
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


def run_worker() -> None:
    """Continuously recover abandoned jobs and process pending work."""
    logger.info("Transaction worker started")

    while True:
        try:
            recover_stale_jobs()
            job_id = claim_one_job()

            if job_id is None:
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            logger.info("Claimed job %s", job_id)
            process_job(job_id)

        except KeyboardInterrupt:
            logger.info("Worker shutdown requested")
            break

        except Exception:
            logger.exception("Unexpected worker loop error")
            time.sleep(2)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    run_worker()
