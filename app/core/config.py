
import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    APP_NAME: str = "Transaction Webhook Processor"
    MAX_ATTEMPTS: int = 3
    RECOVERY_TIMEOUT_SECONDS: int = 60


settings = Settings()
