import os
from typing import List, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError


load_dotenv()


class Settings(BaseModel):
    # Telegram parent bot
    parent_bot_token: str = Field(..., alias="PARENT_BOT_TOKEN")

    # DB
    database_url: str = Field("sqlite+aiosqlite:///./saas.db", alias="DATABASE_URL")

    # Приватный канал, где живёт parent-бот
    private_channel_id: int = Field(..., alias="PRIVATE_CHANNEL_ID")

    # Админы GA (список Telegram ID)
    ga_admin_ids: List[int] = Field(default_factory=list, alias="GA_ADMIN_IDS")

    # Postbacks / FastAPI
    postback_base: str = Field("http://localhost:8000", alias="POSTBACK_BASE")

    # Язык по умолчанию
    lang_default: str = Field("ru", alias="LANG_DEFAULT")

    # Общий URL поддержки по умолчанию
    default_support_url: Optional[str] = Field(None, alias="DEFAULT_SUPPORT_URL")

    # Соль для генерации click_id / сигнатур
    click_salt: str = Field(
        "change_me_to_random_secure_string",
        alias="CLICK_SALT",
    )

    @staticmethod
    def _parse_int_list(value: str | None) -> list[int]:
        """Парсим строку вида '1,2,3' в список [1, 2, 3]."""
        if not value:
            return []
        items = [x.strip() for x in value.split(",") if x.strip()]
        result: list[int] = []
        for item in items:
            try:
                result.append(int(item))
            except ValueError:
                continue
        return result

    @classmethod
    def load(cls) -> "Settings":
        # Базовые значения из окружения (без GA_ADMIN_IDS)
        raw = {
            "PARENT_BOT_TOKEN": os.getenv("PARENT_BOT_TOKEN", ""),
            "DATABASE_URL": os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./saas.db"),
            "PRIVATE_CHANNEL_ID": os.getenv("PRIVATE_CHANNEL_ID", "0"),
            "POSTBACK_BASE": os.getenv("POSTBACK_BASE", "http://localhost:8000"),
            "LANG_DEFAULT": os.getenv("LANG_DEFAULT", "ru"),
            "DEFAULT_SUPPORT_URL": os.getenv("DEFAULT_SUPPORT_URL"),
            "CLICK_SALT": os.getenv(
                "CLICK_SALT",
                "change_me_to_random_secure_string",
            ),
        }

        # Отдельно парсим GA_ADMIN_IDS
        ga_ids_raw = os.getenv("GA_ADMIN_IDS", "")
        ga_ids = cls._parse_int_list(ga_ids_raw)

        try:
            settings = cls(
                **raw,
                GA_ADMIN_IDS=ga_ids,
            )
        except ValidationError as e:
            raise RuntimeError(f"Settings validation error: {e}") from e

        return settings


settings = Settings.load()