from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    database_url: str = "postgresql+psycopg://tutor:tutor@localhost:5440/voice_tutor"
    data_dir: Path = ROOT / "data"

    azure_openai_api_key: str
    azure_openai_endpoint: str
    azure_openai_api_version: str = "2024-10-21"
    azure_openai_deployment: str = "gpt-4o"

    # Which provider implementation to use; see app/providers/__init__.py
    stt_provider: str = "gnani"
    tts_provider: str = "gnani"

    gnani_api_key: str = ""
    gnani_tts_model: str = "timbre-v3.0"
    gnani_tts_voice: str = "Shivani"
    gnani_tts_language: str = "hi-en"
    gnani_stt_language: str = "hi-IN"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def lessons_dir(self) -> Path:
        return self.data_dir / "lessons"


settings = Settings()
