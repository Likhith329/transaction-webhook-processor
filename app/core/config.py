
import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    APP_NAME: str = "Transaction Webhook Processor"
    MAX_ATTEMPTS: int = int(os.getenv("MAX_ATTEMPTS", "3"))
    RECOVERY_TIMEOUT_SECONDS: int = int(os.getenv("RECOVERY_TIMEOUT_SECONDS", "60"))
    WORKER_CONCURRENCY: int = int(os.getenv("WORKER_CONCURRENCY", "50"))
    PROCESSING_DELAY_SECONDS: float = float(os.getenv("PROCESSING_DELAY_SECONDS", "30.0"))
    POLL_INTERVAL_SECONDS: float = float(os.getenv("POLL_INTERVAL_SECONDS", "1.0"))
    RECOVERY_INTERVAL_SECONDS: float = float(os.getenv("RECOVERY_INTERVAL_SECONDS", "30.0"))
    DB_POOL_SIZE: int = int(os.getenv("DB_POOL_SIZE", "30"))
    DB_MAX_OVERFLOW: int = int(os.getenv("DB_MAX_OVERFLOW", "20"))


settings = Settings()
