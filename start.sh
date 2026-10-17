#!/bin/bash
set -e

echo "=== Starting Transaction Webhook Processor on Render ==="

# 1. Run database migrations on external database (Supabase)
echo "Applying database migrations..."
if alembic upgrade head; then
    echo "Database migrations applied successfully."
else
    echo "Warning: Database migrations failed. Continuing startup..."
fi

# 2. Start the background worker process with concurrent thread pool
echo "Starting background worker..."
python -m app.worker.processor &
WORKER_PID=$!
echo "Worker process started with PID: $WORKER_PID"

# Signal handler to stop worker and web server cleanly on shutdown
terminate() {
    echo "Received termination signal. Shutting down gracefully..."
    kill -TERM "$WORKER_PID" 2>/dev/null || true
    kill -TERM "$UVICORN_PID" 2>/dev/null || true
    wait "$WORKER_PID" 2>/dev/null || true
    wait "$UVICORN_PID" 2>/dev/null || true
    exit 0
}

trap terminate SIGTERM SIGINT

# 3. Start the FastAPI application on Render's dynamic $PORT
PORT="${PORT:-8000}"
echo "Starting API web server on port $PORT..."
uvicorn app.main:app --host 0.0.0.0 --port "$PORT" &
UVICORN_PID=$!
echo "Web server started with PID: $UVICORN_PID"

# Wait for either process to exit; if one terminates, shutdown the other
wait -n "$WORKER_PID" "$UVICORN_PID" || true
terminate
