
from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import OutboxJob, Transaction
from app.db.session import SessionLocal
from app.schemas.transaction import TransactionWebhook

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post(
    "/v1/webhooks/transactions",
    status_code=status.HTTP_202_ACCEPTED,
)
def receive_transaction_webhook(
    payload: TransactionWebhook,
    response: Response,
    db: Session = Depends(get_db),
):
    transaction_stmt = (
        insert(Transaction)
        .values(
            transaction_id=payload.transaction_id,
            source_account=payload.source_account,
            destination_account=payload.destination_account,
            amount=payload.amount,
            currency=payload.currency,
            status="PROCESSING",
        )
        .on_conflict_do_nothing(
            index_elements=[Transaction.transaction_id]
        )
        .returning(Transaction.transaction_id)
    )

    inserted_id = db.execute(transaction_stmt).scalar_one_or_none()

    if inserted_id is not None:
        job_stmt = insert(OutboxJob).values(
            transaction_id=inserted_id,
            status="PENDING",
            attempts=0,
        )
        db.execute(job_stmt)

    db.commit()

    response.headers["X-Idempotent-Replay"] = (
        "false" if inserted_id is not None else "true"
    )

    return None
