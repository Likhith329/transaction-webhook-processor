
from datetime import datetime, timezone

from fastapi import FastAPI

from app.api.webhooks import router as webhook_router
from app.api.transactions import router as transaction_router

app = FastAPI(
    title="Transaction Webhook Processor",
    version="1.0.0",
)


@app.get("/")
def health_check():
    return {
        "status": "HEALTHY",
        "current_time": datetime.now(timezone.utc),
    }


app.include_router(webhook_router)
app.include_router(transaction_router)
