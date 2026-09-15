import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

test_database = Path(tempfile.gettempdir()) / "spectrum-api-tests.sqlite"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{test_database.as_posix()}")

from app.database import crud
from app.database.connection import Base, SessionLocal, engine
from app.main import app
from app.services import live_session_store as store

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


def test_health_reports_forced_hindi_transcription():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "project": "Spectrum",
        "ollama_url": "http://localhost:11434",
        "whisper_language": "hi",
        "whisper_task": "transcribe",
    }


def test_claim_response_contains_real_verification_fields():
    db = SessionLocal()
    try:
        audio = crud.create_audio_record(db, "speech.wav", "/tmp/speech.wav")
        claim = crud.create_claim(db, audio.id, "भारत की जनसंख्या दुनिया में सबसे अधिक है।", "Speaker A", "00:14")
        crud.update_claim_verification(
            db,
            claim.id,
            "TRUE",
            "No Reliable Update",
            "UN population estimates support the claim.",
            0.95,
            None,
            "completed",
        )
        crud.replace_sources(db, claim.id, [{"title": "UN Population Division", "url": "https://www.un.org/example", "snippet": "Population estimates."}])
        response = client.get(f"/api/claims/{audio.id}")
    finally:
        db.close()

    assert response.status_code == 200
    result = response.json()["claims"][0]
    assert result["verdict"] == "SUPPORTED"
    assert result["confidence"] == 0.95
    assert result["verification_status"] == "completed"
    assert result["sources"][0]["url"] == "https://www.un.org/example"


def test_live_stream_accepts_generic_http_url_and_preserves_query_params(monkeypatch):
    class FakeProc:
        returncode = 0
        stdout = '{"streams":[{"index":0,"codec_type":"audio","sample_rate":"16000","channels":1,"codec_name":"aac"}]}'
        stderr = ''

    monkeypatch.setattr("app.api.live.subprocess.run", lambda *args, **kwargs: FakeProc())

    response = client.post("/api/live/stream", json={"url": "https://example.com/live/playlist.m3u8?token=abc123"})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["session_id"]
    assert payload["audio_id"] is not None


def test_live_stream_rejects_bad_urls_with_structured_error_code():
    response = client.post("/api/live/stream", json={"url": "ftp://example.com/stream.mp3"})

    assert response.status_code == 400
    payload = response.json()
    assert payload["error_code"] == "INVALID_URL"


def test_live_session_poll_ignores_malformed_claim_records():
    session_id = store.create_session("microphone")
    store._sessions[session_id]["claims"] = [
        {"claim_id": "1", "claim_text": "A government program was launched.", "status": "pending"},
        ["claim_id", "2"],
    ]

    response = client.get(f"/api/live/{session_id}")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["claims"]) == 1
    assert payload["claims"][0]["claim_id"] == "1"
