# Transaction Webhook Processor

A reliable, asynchronous transaction webhook processing service built with **FastAPI, PostgreSQL, SQLAlchemy, and Docker**.

The service acknowledges incoming transaction webhooks immediately and processes them in the background after a simulated 30-second delay. It uses PostgreSQL-backed job storage and idempotency controls to prevent duplicate transactions and improve reliability.

## Features

- **Fast webhook acknowledgment:** Returns HTTP `202 Accepted` without waiting for background processing.
- **Asynchronous processing:** A separate worker simulates an external API call using a 30-second delay.
- **Idempotency:** Duplicate webhook deliveries with the same `transaction_id` do not create duplicate transactions or jobs.
- **Persistent storage:** PostgreSQL stores transaction data, processing status, timestamps, and background job state.
- **Transactional outbox pattern:** The transaction and its background job are created within the same database transaction.
- **Concurrent job claiming:** PostgreSQL row locking with `FOR UPDATE SKIP LOCKED` prevents workers from claiming the same pending job simultaneously.
- **Failure recovery:** Failed jobs can be retried, and stale processing jobs can be recovered.
- **Database migrations:** Alembic manages database schema changes.
- **Containerized services:** Docker Compose runs PostgreSQL, the API, and the background worker.

## Tech Stack

- Python 3.12
- FastAPI
- PostgreSQL 16
- SQLAlchemy
- Alembic
- Psycopg
- Docker and Docker Compose
- Pytest and HTTPX

## Architecture

The application consists of three main services:

1. **FastAPI application:** Validates incoming webhooks, persists transaction records and jobs, and exposes health and transaction status endpoints.
2. **PostgreSQL:** Stores transactions and their corresponding background jobs.
3. **Background worker:** Claims pending jobs, simulates processing for 30 seconds, updates transaction status, and handles failures and recovery.

The API and worker share the same PostgreSQL database. The transaction and job are inserted atomically so that a successfully committed transaction is not left without its corresponding processing job.

## API Endpoints

### 1. Health Check

`GET /`

Returns the service health and current UTC time.

Example response:

```json
{
  "status": "HEALTHY",
  "current_time": "2026-10-17T10:30:00Z"
}
```

### 2. Receive Transaction Webhook

`POST /v1/webhooks/transactions`

Request body:

```json
{
  "transaction_id": "txn_abc123def456",
  "source_account": "acc_user_789",
  "destination_account": "acc_merchant_456",
  "amount": "1500.00",
  "currency": "INR"
}
```

Example request:

```bash
curl -i -X POST http://localhost:8000/v1/webhooks/transactions \
  -H "Content-Type: application/json" \
  -d '{
    "transaction_id": "txn_abc123def456",
    "source_account": "acc_user_789",
    "destination_account": "acc_merchant_456",
    "amount": "1500.00",
    "currency": "INR"
  }'
```

Expected behavior:

- Returns HTTP `202 Accepted`.
- Does not wait for the simulated 30-second processing operation.
- Creates a transaction and a pending job atomically.
- Treats repeated deliveries of the same transaction ID as idempotent requests.

The response includes an `X-Idempotent-Replay` header: `false` for a newly inserted transaction and `true` when the transaction already exists.

### 3. Retrieve Transaction Status

`GET /v1/transactions/{transaction_id}`

Example:

```bash
curl http://localhost:8000/v1/transactions/txn_abc123def456
```

The response contains the transaction details, current status, creation time, and processing completion time.

Possible transaction statuses:

- `PROCESSING`
- `PROCESSED`
- `FAILED`

A transaction typically transitions to `PROCESSED` approximately 30 seconds after the worker starts processing it. The `processed_at` field remains null until processing completes.

## Database Design

### `transactions`

Stores transaction details and processing results.

Important fields:

- `id`: Internal primary key.
- `transaction_id`: Unique external transaction identifier.
- `source_account` and `destination_account`: Account identifiers.
- `amount` and `currency`: Transaction value and currency.
- `status`: Current transaction status.
- `created_at`: Transaction creation timestamp.
- `processed_at`: Processing completion timestamp.

### `outbox_jobs`

Stores durable background processing jobs.

Important fields:

- `id`: Internal job primary key.
- `transaction_id`: Unique reference to a transaction.
- `status`: Job state (`PENDING`, `PROCESSING`, `COMPLETED`, or `FAILED`).
- `attempts`: Number of processing attempts.
- `locked_at`: Timestamp when the job was claimed.
- `last_error`: Most recent recorded processing error.
- `created_at` and `updated_at`: Job timestamps.

## Technical Design Decisions

### Transactional outbox

The webhook handler inserts the transaction and its outbox job within a single database transaction. If the operation fails before commit, neither record is committed. This avoids losing a background job after accepting and persisting a transaction.

### Idempotency

The database enforces uniqueness on `transactions.transaction_id`. PostgreSQL's conflict handling allows repeated webhook deliveries without inserting another transaction or job.

### Background processing

The worker polls PostgreSQL for pending jobs, claims jobs using row-level locking, and performs the simulated external operation outside the database transaction. It then persists the final transaction and job statuses.

### Recovery and retries

The worker recovers stale processing jobs and retries eligible failures up to the configured attempt limit. Jobs that exhaust the limit are marked failed.

This implementation simulates external processing; it does not integrate with a real payment provider.

## Prerequisites

- Python 3.12
- Docker Engine
- Docker Compose
- Git

## Configuration

Create your local environment file from the example:

```bash
cp .env.example .env
```

Configure the PostgreSQL credentials and `DATABASE_URL` in `.env`. Keep `.env` private and never commit it to version control.

The host database connection uses port `5434` in the provided Docker Compose configuration. Containers connect to PostgreSQL using the internal service hostname and port `5432`.

## Run with Docker Compose

From the project root:

```bash
# Start PostgreSQL
sudo docker compose up -d db

# Build the application image
sudo docker compose build api

# Apply database migrations
sudo docker compose run --rm api alembic upgrade head

# Start the API and background worker
sudo docker compose up -d api worker
```

Check the service status:

```bash
sudo docker compose ps
```

Check API health:

```bash
curl http://localhost:8000/
```

Interactive API documentation is available at:

`http://localhost:8000/docs`

View application and worker logs:

```bash
sudo docker compose logs -f api worker
```

## Run Tests

The test suite uses the database configured in `.env`. Ensure PostgreSQL is running, the database schema is migrated, and the environment is configured before running tests.

Activate the Python virtual environment and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run migrations if needed:

```bash
alembic upgrade head
```

Run the tests:

```bash
pytest -q
```

The tests cover the health endpoint, request validation, duplicate webhook handling, database uniqueness, and webhook response time.

## Project Structure

```text
transaction-webhook-processor/
├── alembic/
│   └── versions/
├── app/
│   ├── api/
│   │   ├── transactions.py
│   │   └── webhooks.py
│   ├── core/
│   │   └── config.py
│   ├── db/
│   │   ├── models.py
│   │   └── session.py
│   ├── schemas/
│   │   └── transaction.py
│   ├── worker/
│   │   └── processor.py
│   └── main.py
├── tests/
│   └── test_api.py
├── .env.example
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── alembic.ini
└── requirements.txt
```

## Deployment

For cloud deployment, run the FastAPI application and worker as separate services and configure both to use the same managed PostgreSQL database.

The deployment must apply Alembic migrations before serving requests. Configure database credentials through the hosting platform's environment variables, expose the API over HTTPS, and verify the public health and webhook endpoints after deployment.

**Deployment status:** Local Docker validation is complete. Add the public repository URL and deployed API URL here after they are available.

## Limitations

- The 30-second delay simulates an external operation; no actual payment is executed.
- The worker polls PostgreSQL rather than using a separate message broker.
- The implementation provides durable job storage and retry/recovery behavior, but exactly-once execution of an external side effect would require additional idempotency guarantees from that external system.
