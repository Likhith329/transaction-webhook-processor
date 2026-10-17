
from fastapi.testclient import TestClient
import time
from app.main import app
import uuid

client = TestClient(app)


def test_health_check_returns_healthy():
    response = client.get("/")

    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "HEALTHY"
    assert "current_time" in body


def test_webhook_rejects_missing_required_fields():
    response = client.post(
        "/v1/webhooks/transactions",
        json={"transaction_id": "txn_missing_fields"},
    )

    assert response.status_code == 422


def test_webhook_rejects_invalid_currency():
    response = client.post(
        "/v1/webhooks/transactions",
        json={
            "transaction_id": "txn_invalid_currency",
            "source_account": "acc_source",
            "destination_account": "acc_destination",
            "amount": 100.00,
            "currency": "INR1",
        },
    )

    assert response.status_code == 422


def test_duplicate_webhook_creates_only_one_transaction_and_job():
    from sqlalchemy import func, select

    from app.db.models import OutboxJob, Transaction
    from app.db.session import SessionLocal

    payload = {
        "transaction_id": f"txn_test_idempotency_{uuid.uuid4().hex}",
        "source_account": "acc_source",
        "destination_account": "acc_destination",
        "amount": "100.00",
        "currency": "INR",
    }

    first_response = client.post(
        "/v1/webhooks/transactions", json=payload
    )
    second_response = client.post(
        "/v1/webhooks/transactions", json=payload
    )

    assert first_response.status_code == 202
    assert second_response.status_code == 202
    assert first_response.headers["X-Idempotent-Replay"] == "false"
    assert second_response.headers["X-Idempotent-Replay"] == "true"

    with SessionLocal() as db:
        transaction_count = db.scalar(
            select(func.count())
            .select_from(Transaction)
            .where(
                Transaction.transaction_id == payload["transaction_id"]
            )
        )
        job_count = db.scalar(
            select(func.count())
            .select_from(OutboxJob)
            .where(
                OutboxJob.transaction_id == payload["transaction_id"]
            )
        )

    assert transaction_count == 1
    assert job_count == 1


def test_webhook_returns_202_quickly():
    payload = {
        "transaction_id": f"txn_test_fast_response_{uuid.uuid4().hex}",
        "source_account": "acc_source",
        "destination_account": "acc_destination",
        "amount": "100.00",
        "currency": "INR",
    }

    start = time.perf_counter()

    response = client.post(
        "/v1/webhooks/transactions",
        json=payload,
    )

    elapsed = time.perf_counter() - start

    assert response.status_code == 202
    assert elapsed < 0.5, (
        f"Webhook took {elapsed:.3f}s; expected less than 0.5s"
    )
