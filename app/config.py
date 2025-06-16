from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Telegram
    BOT_TOKEN: str = Field(..., env="BOT_TOKEN")

    # Администратор (может выполнять /add_tutor)
    ADMIN_ID: int = Field(..., env="ADMIN_ID")

    # База данных
    DATABASE_URL: str = Field(..., env="DATABASE_URL")

    # Redis
    REDIS_URL: str = Field(..., env="REDIS_URL")

    # Опциональная старая схема — код доступа и список разрешённых ID
    ALLOWED_TUTOR_IDS: str | None = Field(None, env="ALLOWED_TUTOR_IDS")
    TUTOR_REG_CODE: str | None = Field(None, env="TUTOR_REG_CODE")

    class Config:
        env_file = Path(__file__).parent.parent / ".env"
        env_file_encoding = "utf-8"


settings = Settings()
