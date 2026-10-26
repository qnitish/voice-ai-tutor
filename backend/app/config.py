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
    # Doubt routing only; set to a faster deployment (e.g. gpt-4o-mini) to cut routing latency
    azure_openai_routing_deployment: str | None = None
    # Offline prep (outline, scenes, review, remedy); a reasoning model here catches subject mistakes gpt-4o makes.
    # Set the effort (low|medium|high) only for reasoning models (o-series, gpt-5): they take no temperature.
    azure_openai_prep_deployment: str | None = None
    azure_openai_prep_reasoning_effort: str | None = None

    # Which provider implementation to use; see app/providers/__init__.py
    stt_provider: str = "gnani"
    tts_provider: str = "gnani"

    gnani_api_key: str = ""
    gnani_tts_model: str = "timbre-v3.0"
    gnani_tts_voice: str = "Shivani"
    gnani_tts_language: str = "hi-en"
    gnani_stt_language: str = "hi-IN"
    sarvam_api_key: str = ""
    sarvam_stt_model: str = "saaras:v3"
    sarvam_stt_language: str = "hi-IN"
    # Roman-script narration sounds more natural read as en-IN, also for Hinglish
    sarvam_tts_language: str = "en-IN"
    sarvam_tts_model: str = "bulbul:v3"
    # Stock speaker for voices without a Sarvam clone (only when TTS_PROVIDER=sarvam)
    sarvam_tts_speaker: str = "shubh"
    sarvam_concurrency: int = 4
    # Gender of the default voice; prompts and fixed lines use matching first-person verbs
    tutor_gender: str = "female"

    @property
    def routing_deployment(self) -> str:
        return self.azure_openai_routing_deployment or self.azure_openai_deployment

    @property
    def prep_deployment(self) -> str:
        return self.azure_openai_prep_deployment or self.azure_openai_deployment

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def lessons_dir(self) -> Path:
        return self.data_dir / "lessons"


settings = Settings()
