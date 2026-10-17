
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.webhooks import get_db
from app.db.models import Transaction
from app.schemas.transaction import TransactionResponse

router = APIRouter()


@router.get(
    "/v1/transactions/{transaction_id}",
    response_model=TransactionResponse,
)
def get_transaction(
    transaction_id: str,
    db: Session = Depends(get_db),
):
    transaction = (
        db.query(Transaction)
        .filter(Transaction.transaction_id == transaction_id)
        .one_or_none()
    )

    if transaction is None:
        raise HTTPException(
            status_code=404,
            detail="Transaction not found",
        )

    return transaction
