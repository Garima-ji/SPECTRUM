"""
live_session_store.py
─────────────────────
Thread-safe in-memory store for active Live Mode sessions.

Each session is a plain dict held in _sessions[session_id].
All reads and writes go through _lock so background processing
threads and API threads never corrupt shared state.

Sessions are intentionally ephemeral — they live until the backend
process restarts.  Claim/transcript data is also written to the
existing DB tables so it survives restarts.
"""

import threading
import time
import uuid
from typing import Dict, List, Optional


_sessions: Dict[str, dict] = {}
_lock = threading.Lock()


def create_session(source: str) -> str:
    """Allocate a new session and return its UUID."""
    session_id = str(uuid.uuid4())
    with _lock:
        _sessions[session_id] = {
            "session_id": session_id,
            "source": source,           # "microphone" | "system"
            "detected_language": None,   # stable language selected by Whisper
            "status": "capturing",      # capturing | draining | complete | error
            "capture_active": True,
            "capture_done": False,
            "capture_process": None,     # FFmpeg Popen for stream sessions only
            "audio_id": None,           # FK to AudioRecord.id
            "transcript_segments": [],  # list of {start, end, text, speaker}
            "claims": [],               # list of claim dicts (see add_claim)
            "claim_buffer": [],         # ordered Whisper segments awaiting grouping
            "last_chunk_index": -1,
            "processing": False,        # True while a chunk worker thread is running
            "processed_end_time": 0.0,  # audio timestamp up to which we've seen segments
            # Pending chunk slot: holds the latest chunk that arrived while
            # processing=True.  Only the newest chunk is kept (older ones are
            # superseded because the frontend sends the full accumulated blob).
            "pending_chunk": None,      # None | {"path": str, "index": int}
            "unprocessed_text": "",     # Buffer for new transcripts waiting for claim extraction
            "claims_processing": False,  # True while one Ollama claim request is running
            "seen_claims": set(),       # Deduplication set of normalized claim strings
            "error": None,
            "created_at": time.time(),
        }
    return session_id


def get_session(session_id: str) -> Optional[dict]:
    """Return a shallow copy of the session dict, or None if not found."""
    with _lock:
        sess = _sessions.get(session_id)
        if sess is None:
            return None
        # Shallow-copy the top-level dict so callers can't mutate shared state.
        # Nested lists (transcript_segments, claims) are still shared — do NOT
        # mutate them outside this module. Claims are normalized defensively to
        # ensure the live poll endpoint never crashes on malformed entries.
        return {
            **sess,
            "transcript_segments": list(sess["transcript_segments"]),
            "claims": [
                dict(c) if isinstance(c, dict) else {}
                for c in sess["claims"]
                if isinstance(c, dict)
            ],
            "seen_claims": set(sess["seen_claims"]),
        }


def update_session(session_id: str, **kwargs) -> None:
    """Atomically update scalar fields on a session."""
    with _lock:
        if session_id in _sessions:
            _sessions[session_id].update(kwargs)


def add_transcript_segments(session_id: str, segments: List[dict]) -> None:
    """Append new transcript segments (already deduplicated by the caller)."""
    with _lock:
        if session_id in _sessions:
            _sessions[session_id]["transcript_segments"].extend(segments)


def add_claim(session_id: str, claim: dict) -> None:
    """Append a new claim entry to the session."""
    with _lock:
        if session_id in _sessions:
            _sessions[session_id]["claims"].append(claim)


def get_claim_buffer(session_id: str) -> List[dict]:
    """Return a copy of the ordered segments waiting for grouping."""
    with _lock:
        session = _sessions.get(session_id)
        return list(session.get("claim_buffer", [])) if session else []


def append_claim_buffer(session_id: str, segment: dict) -> List[dict]:
    """Append one transcript segment and return the grouping buffer."""
    with _lock:
        session = _sessions.get(session_id)
        if not session:
            return []
        session.setdefault("claim_buffer", []).append(dict(segment))
        return list(session["claim_buffer"])


def take_claim_buffer(session_id: str) -> List[dict]:
    """Atomically take and clear the current grouping buffer."""
    with _lock:
        session = _sessions.get(session_id)
        if not session:
            return []
        buffered = list(session.get("claim_buffer", []))
        session["claim_buffer"] = []
        return buffered


def update_claim(session_id: str, claim_id: str, **kwargs) -> None:
    """Update fields of an existing claim identified by claim_id string."""
    with _lock:
        if session_id not in _sessions:
            return
        for claim in _sessions[session_id]["claims"]:
            if claim.get("claim_id") == claim_id:
                claim.update(kwargs)
                break


def stop_session(session_id: str) -> bool:
    """Stop capture while preserving already queued processing work.

    ``False`` means the session was already terminal, so callers must not
    accidentally turn COMPLETE back into DRAINING on a duplicate stop request.
    """
    with _lock:
        session = _sessions.get(session_id)
        if not session or session["status"] in {"complete", "error"}:
            return False
        session["status"] = "draining"
        session["capture_active"] = False
        session["capture_done"] = True
        return True


def stop_capture_process(session_id: str) -> None:
    """Promptly stop a stream producer without touching fact-check workers."""
    with _lock:
        process = (_sessions.get(session_id) or {}).get("capture_process")
    if process is None:
        return
    try:
        if process.poll() is None:
            process.terminate()
    except (AttributeError, OSError):
        # The capture thread will still observe the drained session and clean up.
        pass


def mark_capture_done(session_id: str) -> None:
    """Mark the audio producer finished without cancelling processing."""
    with _lock:
        if session_id in _sessions:
            _sessions[session_id]["capture_active"] = False
            _sessions[session_id]["capture_done"] = True
            _sessions[session_id]["capture_process"] = None
            if _sessions[session_id]["status"] == "capturing":
                _sessions[session_id]["status"] = "draining"


def complete_session(session_id: str) -> bool:
    """Atomically mark a drained session complete.

    Multiple claim workers can finish together.  Returning whether this call
    performed the transition lets the API finalise the database record once.
    """
    with _lock:
        session = _sessions.get(session_id)
        if not session or session["status"] != "draining":
            return False
        session["status"] = "complete"
        session["capture_active"] = False
        return True


def set_pending_chunk(session_id: str, path: str, index: int) -> None:
    """
    Store the latest incoming chunk path while the session is busy processing
    a previous chunk.  Only the most-recent chunk is kept because the frontend
    sends the FULL accumulated blob each time — the latest always supersedes
    older ones.  The previous pending file (if any) is returned for cleanup.
    """
    with _lock:
        if session_id not in _sessions:
            return
        old = _sessions[session_id].get("pending_chunk")
        _sessions[session_id]["pending_chunk"] = {"path": path, "index": index}
    # Delete the superseded file outside the lock.
    if old:
        import os
        try:
            if os.path.isfile(old["path"]):
                os.unlink(old["path"])
        except OSError:
            pass


def pop_pending_chunk(session_id: str) -> Optional[dict]:
    """
    Atomically retrieve and clear the pending chunk slot.
    Returns {"path": str, "index": int} or None.
    """
    with _lock:
        if session_id not in _sessions:
            return None
        pending = _sessions[session_id].get("pending_chunk")
        _sessions[session_id]["pending_chunk"] = None
        return pending


def append_unprocessed_text(session_id: str, text: str) -> None:
    """Append newly transcribed text to the session's unprocessed buffer."""
    with _lock:
        if session_id in _sessions:
            current = _sessions[session_id].get("unprocessed_text", "")
            _sessions[session_id]["unprocessed_text"] = (current + " " + text).strip()


def clear_unprocessed_text(session_id: str) -> None:
    """Clear the unprocessed text buffer after successful extraction."""
    with _lock:
        if session_id in _sessions:
            _sessions[session_id]["unprocessed_text"] = ""


def take_unprocessed_text(session_id: str) -> str:
    """Atomically take the current claim buffer so new transcript text is preserved."""
    with _lock:
        if session_id not in _sessions:
            return ""
        text = _sessions[session_id].get("unprocessed_text", "")
        _sessions[session_id]["unprocessed_text"] = ""
        return text


def try_start_claim_extraction(session_id: str) -> bool:
    """Claim the single Ollama worker slot for a live session."""
    with _lock:
        session = _sessions.get(session_id)
        if not session or session.get("claims_processing"):
            return False
        session["claims_processing"] = True
        return True


def finish_claim_extraction(session_id: str) -> None:
    """Release the per-session claim extraction worker slot."""
    with _lock:
        if session_id in _sessions:
            _sessions[session_id]["claims_processing"] = False


def add_seen_claim(session_id: str, normalized_claim: str) -> None:
    """Record a claim string as seen to avoid duplicate verification."""
    with _lock:
        if session_id in _sessions:
            _sessions[session_id]["seen_claims"].add(normalized_claim)
