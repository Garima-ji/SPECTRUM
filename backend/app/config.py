import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "Spectrum"
    API_PREFIX: str = "/api"

    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/spectrum"

    OLLAMA_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen2.5:latest"
    OLLAMA_TIMEOUT_SECONDS: int = 300

    # Hindi is deliberately forced so Whisper emits Devanagari rather than
    # auto-detecting a related language and choosing Urdu script.
    WHISPER_MODEL: str = "small"
    WHISPER_DEVICE: str = "cpu"
    WHISPER_COMPUTE_TYPE: str = "int8"
    # Empty string means auto-detect language. Set to 'hi' to force Hindi/Devanagari output.
    WHISPER_LANGUAGE: str = os.getenv("WHISPER_LANGUAGE", "")
    WHISPER_TASK: str = "transcribe"
    
    # Multi-factor Confidence Scoring Weights (Sum = 1.0 / 100%)
    CONFIDENCE_WEIGHT_EVIDENCE_STRENGTH: float = 0.25
    CONFIDENCE_WEIGHT_SOURCE_QUALITY: float = 0.20
    CONFIDENCE_WEIGHT_EVIDENCE_COVERAGE: float = 0.15
    CONFIDENCE_WEIGHT_SOURCE_CONSISTENCY: float = 0.15
    CONFIDENCE_WEIGHT_SOURCE_DIVERSITY: float = 0.10
    CONFIDENCE_WEIGHT_TEMPORAL_RELEVANCE: float = 0.10
    CONFIDENCE_WEIGHT_CLAIM_CLARITY: float = 0.05
    # Retain backward-compatibility alias if referenced
    CONFIDENCE_WEIGHT_LLM_CONFIDENCE: float = 0.0

    # In Docker this resolves to /app, which is also where the data volume is mounted.
    BASE_DIR: str = str(Path(__file__).resolve().parents[1])
    AUDIO_UPLOAD_DIR: str = os.path.join(BASE_DIR, "data", "audio")
    TRANSCRIPTS_DIR: str = os.path.join(BASE_DIR, "data", "transcripts")
    CLAIMS_DIR: str = os.path.join(BASE_DIR, "data", "claims")
    EVIDENCE_DIR: str = os.path.join(BASE_DIR, "data", "evidence")
    RESULTS_DIR: str = os.path.join(BASE_DIR, "data", "results")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()

for directory in (
    settings.AUDIO_UPLOAD_DIR,
    settings.TRANSCRIPTS_DIR,
    settings.CLAIMS_DIR,
    settings.EVIDENCE_DIR,
    settings.RESULTS_DIR,
):
    os.makedirs(directory, exist_ok=True)
