from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", env_file=".env", extra="ignore")

    env: str = "dev"
    log_level: str = "INFO"

    database_url: str
    redis_url: str = "redis://localhost:6379/0"

    cors_origins: str = "http://localhost:5173"

    storage_dir: str = "./data"
    rtmp_base_url: str = "rtmp://nginx-rtmp:1935/live"
    rtmp_stat_url: str = "http://nginx-rtmp:8080/stat"
    processing_tick_sec: int = 1

    # Базовый HTTP URL приложения (как клиент открывает API), для абсолютных ссылок в CSV /media/…
    # В docker-compose по умолчанию http://localhost:8000 (порт хоста).
    public_base_url: str = ""
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 12
    seed_admin_email: str = "admin@example.com"
    seed_admin_password: str = "admin123"
    seed_viewer_enabled: bool = True
    seed_viewer_email: str = "viewer@example.com"
    seed_viewer_password: str = "viewer123"

    @property
    def cors_origin_list(self) -> list[str]:
        # Простая форма: список через запятую
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()  # type: ignore[call-arg]

