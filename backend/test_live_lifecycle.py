"""Regression tests for the capture -> drain -> verify -> complete lifecycle."""

import threading

from app.api import live
from app.services import live_session_store as store


class _FakeSession:
    def close(self):
        pass


def _claim(claim_id: str, text: str, verification_status: str) -> dict:
    return {
        "claim_id": claim_id,
        "db_id": int(claim_id) if claim_id.isdigit() else 1,
        "claim_text": text,
        "verification_status": verification_status,
        "verdict": "PENDING",
        "status": "Retrieving Evidence",
        "sources": [],
    }


def test_stop_capture_keeps_inflight_verification_running(monkeypatch):
    """A Stop request must not turn an in-flight claim into a terminal result."""
    session_id = store.create_session("microphone")
    store.add_claim(session_id, _claim("17", "India has 28 states.", "claim_detected"))

    verification_started = threading.Event()
    finish_verification = threading.Event()

    def slow_verification(*_args, **_kwargs):
        verification_started.set()
        assert finish_verification.wait(1)
        return {
            "verdict": "SUPPORTED",
            "status": "No Reliable Update",
            "reasoning": "Validated source supports the claim.",
            "confidence": 85.0,
            "confidence_factors": None,
            "sources": [{"title": "Official source", "url": "https://www.example.gov/article", "snippet": "28 states"}],
        }

    monkeypatch.setattr(live.verification_service, "verify_single_claim", slow_verification)
    monkeypatch.setattr(live, "SessionLocal", _FakeSession)
    monkeypatch.setattr(live.crud, "update_claim_verification", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(live.crud, "replace_sources", lambda *_args, **_kwargs: None)

    worker = threading.Thread(target=live._verify_claim_bg, args=(session_id, 17, "India has 28 states."))
    worker.start()
    assert verification_started.wait(1)
    assert store.get_session(session_id)["claims"][0]["verification_status"] == "retrieving_evidence"

    assert store.stop_session(session_id) is True
    live._maybe_complete_session(session_id)
    draining = store.get_session(session_id)
    assert draining["status"] == "draining"
    assert draining["claims"][0]["verification_status"] == "retrieving_evidence"

    finish_verification.set()
    worker.join(timeout=1)
    completed = store.get_session(session_id)
    assert not worker.is_alive()
    assert completed["claims"][0]["verification_status"] == "completed"
    assert completed["claims"][0]["verdict"] == "SUPPORTED"
    assert completed["status"] == "complete"


def test_drain_flushes_final_transcript_claim_before_completion(monkeypatch):
    """A transcript appended after Stop still becomes a claim and blocks COMPLETE."""
    session_id = store.create_session("microphone")
    store.update_session(session_id, audio_id=42)
    store.add_claim(session_id, _claim("1", "Existing claim remains in flight.", "retrieving_evidence"))
    store.append_claim_buffer(
        session_id,
        {"start": 4.0, "end": 6.0, "speaker": "Speaker A", "text": "India has 28 states."},
    )
    dispatched = []

    def fake_dispatch(sid, _audio_id, segment):
        dispatched.append(segment["text"])
        store.add_claim(sid, _claim("2", segment["text"], "retrieving_evidence"))

    monkeypatch.setattr(live, "_dispatch_claim_for_segment", fake_dispatch)
    monkeypatch.setattr(live.claim_service, "is_checkworthy", lambda _text: True)
    monkeypatch.setattr(live, "SessionLocal", _FakeSession)
    monkeypatch.setattr(live.crud, "update_audio_record_status", lambda *_args, **_kwargs: None)

    assert store.stop_session(session_id) is True
    # This call models the chunk worker finishing after capture stopped.
    live._maybe_complete_session(session_id)
    assert dispatched == ["India has 28 states."]
    assert store.get_session(session_id)["status"] == "draining"

    store.update_claim(session_id, "1", verification_status="completed", verdict="SUPPORTED")
    live._maybe_complete_session(session_id)
    assert store.get_session(session_id)["status"] == "draining"

    store.update_claim(session_id, "2", verification_status="completed", verdict="SUPPORTED")
    live._maybe_complete_session(session_id)
    completed = store.get_session(session_id)
    assert completed["status"] == "complete"
    assert completed["claims"][1]["claim_text"] == "India has 28 states."
