from pathlib import Path
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
    )

    anthropic_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    dashscope_api_key: Optional[str] = None
    deepseek_api_key: Optional[str] = None
    tushare_token: Optional[str] = None
    log_level: str = Field(default="INFO", validation_alias="FA_LOG_LEVEL")
    cache_dir: Path = Field(
        default=Path.home() / ".financial-analyst" / "cache",
        validation_alias="FA_CACHE_DIR",
    )

    def __init__(self, **kw):
        super().__init__(**kw)
        self.cache_dir = Path(self.cache_dir).expanduser()
