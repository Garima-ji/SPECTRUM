from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import claims, dashboard, live, transcript, upload, verify
from app.config import settings
from app.database.connection import initialize_database
import app.database.models  # Registers model metadata before initialization.
from app.utils.logger import logger

initialize_database()

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="1.1.0",
    description="Spectrum - multilingual evidence-based fact checking",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload.router, prefix=settings.API_PREFIX)
app.include_router(transcript.router, prefix=settings.API_PREFIX)
app.include_router(claims.router, prefix=settings.API_PREFIX)
app.include_router(verify.router, prefix=settings.API_PREFIX)
app.include_router(dashboard.router, prefix=settings.API_PREFIX)
app.include_router(live.router, prefix=settings.API_PREFIX)


@app.on_event("startup")
def preload_whisper_model() -> None:
    """Load Faster-Whisper once at process start so live chunks never pay cold-start."""
    try:
        from app.services.whisper_service import get_whisper_model

        get_whisper_model()
        logger.info("[Startup] Whisper model preloaded for live mode.")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Startup] Whisper preload skipped: %s", exc)


@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "project": settings.PROJECT_NAME,
        "ollama_url": settings.OLLAMA_URL,
        "whisper_language": settings.WHISPER_LANGUAGE,
        "whisper_task": settings.WHISPER_TASK,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
