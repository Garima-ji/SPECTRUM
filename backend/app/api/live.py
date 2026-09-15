"""
live.py — Live Mode API router
──────────────────────────────
Four endpoints that together form the Live Mode pipeline.

    POST /api/live/start        Create session + AudioRecord; return session_id
    POST /api/live/chunk        Receive full-accumulated audio blob; transcribe async
    GET  /api/live/{session_id} Return current session state (transcript + claims)
    POST /api/live/stop         Mark session stopped; finalise AudioRecord in DB

Design notes
────────────
• The frontend sends the FULL accumulated audio blob every ~8 s (not raw
  individual chunks).  Because WebM/Opus individual MediaRecorder slices may
  not be independently decodable by ffmpeg/Whisper, sending the entire Blob
  ([...allChunks]) built from all collected chunks guarantees a valid container
  with the codec init header always present.

• Whisper processes the whole blob each time via the existing singleton
  (whisper_service.transcribe_audio).  The backend tracks `processed_end_time`
  and only adds segments whose start timestamp is beyond the already-seen range,
  preventing duplicates.

• Transcription runs in a plain daemon thread so the POST /chunk endpoint
  returns immediately — live transcript display is NEVER blocked by Whisper.

• Claim extraction runs in its OWN daemon thread, separate from the Whisper
  transcription thread.  The `processing` flag is cleared as soon as Whisper
  finishes and transcript segments are stored — Ollama NEVER blocks the next
  audio chunk.

• Fact-checking (verification) also runs in its own daemon thread per claim.

• The existing /api/upload, /api/transcript, /api/claims, and /api/verify
  endpoints are completely untouched by this module.
"""

import logging
import os
import queue
import tempfile
import threading
import time
import subprocess
import shutil
import audioop
import wave
import json
import urllib.parse
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import crud
from app.database.connection import SessionLocal, get_db
from app.services import claim_service, verification_service, whisper_service
from app.services import live_session_store as store
from app.services.live_session_store import (
    pop_pending_chunk,
    set_pending_chunk,
    add_seen_claim,
    append_claim_buffer,
    get_claim_buffer,
    take_claim_buffer,
)
from app.services.diarization_service import assign_speakers
from app.utils.helpers import normalize_verdict

logger = logging.getLogger("spectrum")
router = APIRouter(prefix="/live", tags=["Live Mode"])

# Retrieval may run for every detected claim, but Qwen inference is bounded so
# CPU-heavy model calls do not starve transcription or create an unbounded pile
# of competing requests.
_LIVE_VERIFY_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="live-verify")
_LIVE_QWEN_SEMAPHORE = threading.Semaphore(1)  # CPU can only run 1 Qwen inference at a time
_LIVE_RETRIEVAL_TIMEOUT_SECONDS = 15
_LIVE_QWEN_TIMEOUT_SECONDS = 180  # CPU inference can take 100-110s; 90s was too short
_SESSION_STREAM_QUEUES: dict[str, queue.Queue] = {}


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────

def _verify_claim_bg(session_id: str, db_id: int, claim_text: str) -> None:
    """
    Background thread: run verification for one claim and persist the result.

    This runs CONCURRENTLY with live transcription — it must never block the
    chunk processing thread.  Failures are caught and stored as UNVERIFIABLE.
    """
    _lang = "hi" if re.search(r"[\u0900-\u097F]", claim_text) else "en"
    logger.info("[PIPELINE] CLAIM_CREATED | claim_id=%s | language=%s | claim=%s", db_id, _lang, claim_text)
    logger.info("CLAIM_CREATED\nclaim: %s", claim_text)
    logger.info("[Live][Verify] Starting for claim_id=%s in session %s", db_id, session_id)
    t0 = time.perf_counter()

    def _on_status(stage: str, label: str) -> None:
        try:
            store.update_claim(session_id, str(db_id), verification_status=stage, status=label)
        except Exception:
            pass

    try:
        store.update_claim(session_id, str(db_id), verification_status="claim_detected", status="Claim Detected")
        result = verification_service.verify_single_claim(
            claim_text,
            generate_query=False,
            live_mode=True,
            qwen_semaphore=_LIVE_QWEN_SEMAPHORE,
            retrieval_timeout_seconds=_LIVE_RETRIEVAL_TIMEOUT_SECONDS,
            qwen_timeout_seconds=_LIVE_QWEN_TIMEOUT_SECONDS,
            on_status=_on_status,
        )
        terminal_status = "failed" if result.get("error_type") else "completed"
        logger.info("[PIPELINE] VERDICT_READY | claim_id=%s | verdict=%s | confidence=%s", db_id, result.get("verdict"), result.get("confidence"))
        db = SessionLocal()
        try:
            crud.update_claim_verification(
                db,
                db_id,
                verdict=result["verdict"],
                status=result["status"],
                reasoning=result["reasoning"],
                confidence=result["confidence"],
                confidence_factors=result.get("confidence_factors"),
                verification_status=terminal_status,
            )
            crud.replace_sources(db, db_id, result["sources"])
        finally:
            db.close()

        # Also update in-memory store so the next poll reflects the result.
        try:
            store.update_claim(
                session_id,
                str(db_id),
                verdict=result["verdict"],
                status=result["status"],
                reasoning=result["reasoning"],
                confidence=result["confidence"],
                confidence_factors=result.get("confidence_factors"),
                verification_status=terminal_status,
                sources=result["sources"],
            )
        except Exception:
            pass  # Session may have been removed; DB is already updated.

        elapsed = time.perf_counter() - t0
        _timing = result.get("timing") or {}
        logger.info(
            "[Live] Verification completed in %.2fs for claim ID=%s verdict=%s confidence=%.3f",
            elapsed,
            db_id,
            result["verdict"],
            result.get("confidence", 0.0),
        )
        logger.info(
            "[TIMING] claim_id=%s retrieval=%.2fs qwen=%.2fs total=%.2fs",
            db_id,
            _timing.get("retrieval_s", 0.0),
            _timing.get("qwen_s", 0.0),
            elapsed,
        )

    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "[Live][Verify][ERROR] Failed for claim_id=%s session=%s: %s",
            db_id, session_id, exc,
        )
        db = SessionLocal()
        try:
            crud.update_claim_verification(
                db, db_id,
                verdict="INSUFFICIENT EVIDENCE",
                status="VERIFICATION_TIMEOUT" if isinstance(exc, TimeoutError) else "Verification Failed",
                reasoning=f"Verification failed: {exc}",
                confidence=0.0,
                confidence_factors=None,
                verification_status="failed",
            )
        finally:
            db.close()
        try:
            store.update_claim(
                session_id, str(db_id),
                verdict="INSUFFICIENT EVIDENCE",
                status="VERIFICATION_TIMEOUT" if isinstance(exc, TimeoutError) else "Verification Failed",
                verification_status="failed",
                confidence=0.0,
                confidence_factors=None,
                reasoning=f"Verification failed: {exc}",
            )
        except Exception:
            pass
    finally:
        _maybe_complete_session(session_id)


def _maybe_complete_session(session_id: str) -> None:
    """Complete only after capture, transcription, and all claim jobs drain."""
    session = store.get_session(session_id)
    if not session or session.get("status") != "draining":
        return
    q = _SESSION_STREAM_QUEUES.get(session_id)
    if q is not None and not q.empty():
        return
    if not session.get("capture_done") or session.get("processing") or session.get("pending_chunk"):
        return
    if session.get("claim_buffer"):
        _flush_claim_buffer(session_id, session.get("audio_id"))
        session = store.get_session(session_id)
        if not session or session.get("processing") or session.get("pending_chunk"):
            return
    if any(
        claim.get("verification_status") not in {"completed", "failed"}
        for claim in session.get("claims", [])
    ):
        return
    if not store.complete_session(session_id):
        return
    audio_id = session.get("audio_id")
    if audio_id:
        segments = session.get("transcript_segments", [])
        transcript = "\n".join(
            f"[{seg.get('speaker', 'Speaker')} {_fmt_time(seg['start'])}] {seg['text']}"
            for seg in segments
        ) if segments else ""
        db = SessionLocal()
        try:
            crud.update_audio_record_status(
                db,
                audio_id,
                status="completed",
                transcript_text=transcript or "(no speech detected)",
                duration=float(segments[-1]["end"]) if segments else 0.0,
            )
        finally:
            db.close()
    logger.info("[Live] Session complete after draining: session_id=%s", session_id)


def _audio_duration_seconds(audio_path: str) -> float | None:
    """Return container duration so a trimmed microphone window keeps its clock."""
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", audio_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=True,
            timeout=5,
        )
        duration = float((probe.stdout or "").strip())
        return duration if duration > 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _normalized_transcript_text(text: str) -> str:
    """Normalize Latin/Devanagari text for overlap-only duplicate detection."""
    return re.sub(r"[^\w\s\u0900-\u097F]", "", str(text or "").casefold()).strip()


def _is_duplicate_transcript_segment(segment: dict, previous_segments: list[dict]) -> bool:
    """Suppress overlapping Whisper re-decodes near the boundary.
    Never suppress segments based on distant history or when new text contains more content.
    """
    text = _normalized_transcript_text(segment.get("text", ""))
    if not text or len(text) < 2:
        return True
    seg_start = float(segment.get("start", 0.0))
    from difflib import SequenceMatcher

    # Only compare against recent segments near the boundary (within 4 seconds)
    recent_candidates = [
        p for p in previous_segments[-10:]
        if abs(float(p.get("end", 0.0)) - seg_start) <= 4.0 or abs(float(p.get("start", 0.0)) - seg_start) <= 3.0
    ]

    for previous in reversed(recent_candidates):
        previous_text = _normalized_transcript_text(previous.get("text", ""))
        if not previous_text:
            continue
        if text == previous_text:
            return True
        # Only suppress if this segment's text is fully contained inside an already-added boundary segment
        if len(text) >= 5 and text in previous_text:
            return True
        if len(text) >= 6 and len(previous_text) >= 6 and SequenceMatcher(None, text, previous_text).ratio() >= 0.85:
            return True
    return False



_CLAIM_BUFFER_MAX_SECONDS = 25.0
_CONTINUATION_WORDS = {
    # Prepositions
    "a", "an", "and", "as", "at", "because", "but", "by", "for", "from",
    "he", "her", "if", "in", "into", "is", "it", "of", "on", "or", "that",
    "the", "their", "they", "to", "was", "were", "which", "who", "with",
    "about", "above", "across", "after", "against", "along", "among", "around",
    "before", "behind", "below", "beneath", "beside", "between", "beyond",
    "during", "except", "inside", "near", "off", "onto", "out", "outside",
    "over", "past", "since", "through", "throughout", "toward", "towards",
    "under", "underneath", "until", "unto", "up", "upon", "within", "without",
    # Conjunctions / Relatives / Pronouns
    "although", "though", "while", "whereas", "whether", "so", "than",
    "whom", "whose", "where", "when", "why", "how", "nor", "yet",
    "this", "these", "those", "my", "your", "his", "our", "its",
    "any", "some", "every", "each", "all", "both", "either", "neither",
    # Auxiliaries / Incompletes
    "be", "been", "being", "have", "has", "had", "do", "does", "did",
    "can", "could", "will", "would", "shall", "should", "may", "might", "must",
    "going", "want", "wants", "trying", "need", "needs", "sort", "kind",
}
_HINDI_CONTINUATION_WORDS = {
    "और", "कि", "से", "में", "पर", "को", "ने", "का", "के", "की",
    "या", "तथा", "एवं", "लेकिन", "मगर", "किंतु", "परंतु", "क्योंकि", "चूंकि", "ताकि",
    "जब", "तब", "यदि", "अगर", "तो", "जैसे", "वैसे", "जिसने", "जिसका", "जिसके", "जिसकी",
    "जिन्हें", "जिन्होंने", "जो", "वह", "यह", "वे", "ये", "इस", "उस", "इन", "उन",
    "होने", "करते", "करती", "करने", "रहे", "रही", "रहा", "गए", "गई", "गया",
}


def _combine_claim_segments(segments: list[dict]) -> dict:
    """Combine adjacent Whisper segments without rewriting their words."""
    return {
        "text": " ".join(str(segment.get("text", "")).strip() for segment in segments).strip(),
        "start": float(segments[0].get("start", 0.0)),
        "end": float(segments[-1].get("end", segments[-1].get("start", 0.0))),
        "speaker": segments[0].get("speaker"),
    }


def _is_trailing_continuation(text: str) -> bool:
    clean = re.sub(r"[^\w\s\u0900-\u097F]", "", text).strip()
    words = clean.split()
    if not words:
        return False
    last = words[-1].casefold()
    return last in _CONTINUATION_WORDS or last in _HINDI_CONTINUATION_WORDS


def _group_should_flush(segments: list[dict]) -> bool:
    if not segments:
        return False
    candidate = _combine_claim_segments(segments)
    text = candidate["text"]
    words = text.split()
    duration = candidate["end"] - candidate["start"]
    trailing = _is_trailing_continuation(text)

    # 1) Terminal punctuation: flush ONLY if not ending on a continuation word
    if re.search(r"[.!?\u0964\u0965]$", text):
        if not trailing and len(words) >= 4:
            return True

    # 2) Buffer duration exceeded or long thought: force flush
    if duration >= _CLAIM_BUFFER_MAX_SECONDS or len(words) >= 30:
        if not trailing or len(words) >= 35:
            return True

    return False


def _normalize_claim_text(text: str) -> str:
    return re.sub(r"[^\w\s]", "", text.casefold()).strip()


def _claim_is_duplicate(session: dict, normalized_claim: str) -> bool:
    from difflib import SequenceMatcher

    return any(
        SequenceMatcher(None, normalized_claim, existing).ratio() >= 0.92
        for existing in session.get("seen_claims", set())
    )


_CONVERSATIONAL_PREFIXES = re.compile(
    r"^(well|and|but|so|now|in\s+fact|actually|like\s+i\s+said|as\s+we\s+know|look|clearly|obviously|action|hey|listen|you\s+know|you\s+see|i\s+mean|okay|right|yes|no|oh|by\s+the\s+clovis)[,\s!]+",
    re.IGNORECASE,
)


def _clean_claim_candidate(text: str) -> str:
    cleaned = text.strip()
    while True:
        m = _CONVERSATIONAL_PREFIXES.match(cleaned)
        if not m:
            break
        cleaned = cleaned[m.end():].strip()
    if cleaned:
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned


def _dispatch_claim_immediately(session_id: str, audio_id: int, segment: dict) -> None:
    """
    Instantly create and register a checkworthy claim (<1ms), and submit
    background verification. Zero latency, zero Ollama bottleneck.
    """
    raw_text = str(segment.get("text", "")).strip()
    if not raw_text or _is_trailing_continuation(raw_text) or not claim_service.is_checkworthy(raw_text):
        return

    claim_text = _clean_claim_candidate(raw_text)
    if len(claim_text.split()) < 6 or not claim_service.is_checkworthy(claim_text):
        return

    normalized_claim = _normalize_claim_text(claim_text)
    session = store.get_session(session_id)
    if not session or _claim_is_duplicate(session, normalized_claim):
        return

    add_seen_claim(session_id, normalized_claim)
    db = SessionLocal()
    try:
        timestamp = f"{int(float(segment.get('start', 0)) // 60):02d}:{int(float(segment.get('start', 0)) % 60):02d}"
        speaker = segment.get("speaker")
        db_claim = crud.create_claim(db, audio_id=audio_id, claim_text=claim_text,
                                     speaker=speaker, timestamp=timestamp)
        crud.update_claim_verification(db, db_claim.id, verdict="PENDING",
                           status="Claim Detected", reasoning="Verification queued.",
                                       confidence=None, confidence_factors=None,
                           verification_status="claim_detected")
        store.add_claim(session_id, {
            "claim_id": str(db_claim.id),
            "db_id": db_claim.id,
            "claim_text": claim_text,
            "speaker": speaker,
            "timestamp": timestamp,
            "verification_status": "claim_detected",
            "verdict": "PENDING",
            "confidence": None,
            "reasoning": None,
            "sources": [],
            "status": "Claim Detected",
        })
        _LIVE_VERIFY_EXECUTOR.submit(_verify_claim_bg, session_id, db_claim.id, claim_text)
        logger.info("[Live] Claim detected INSTANTLY (<1ms): claim_id=%s session=%s text=%.100s", db_claim.id, session_id, claim_text)
    except Exception as exc:  # noqa: BLE001
        logger.exception("[Live][Claims] DB persistence failed for session=%s: %s", session_id, exc)
    finally:
        db.close()


def _dispatch_grouped_claims(session_id: str, audio_id: int, segments: list[dict]) -> None:
    """Accumulate Whisper segments and dispatch only complete thought groups."""
    _t0 = time.perf_counter()
    for segment in segments:
        buffered = get_claim_buffer(session_id)
        # A VAD gap (pause in speech >= 0.8s) indicates a natural sentence boundary
        if buffered and float(segment.get("start", 0.0)) - float(buffered[-1].get("end", 0.0)) >= 0.8:
            candidate = _combine_claim_segments(buffered)
            take_claim_buffer(session_id)
            _dispatch_claim_immediately(session_id, audio_id, candidate)

        buffered = append_claim_buffer(session_id, segment)
        if _group_should_flush(buffered):
            candidate = _combine_claim_segments(take_claim_buffer(session_id))
            _dispatch_claim_immediately(session_id, audio_id, candidate)

    logger.info(
        "[Live] Grouped %d Whisper segments; %d remain buffered for context in session=%s",
        len(segments), len(get_claim_buffer(session_id)), session_id,
    )
    logger.info(
        "[TIMING] Claim detection time: %.3fs for %d segment(s) in session=%s",
        time.perf_counter() - _t0, len(segments), session_id,
    )


def _flush_claim_buffer(session_id: str, audio_id: int | None) -> None:
    """Flush a pending thought at a silence or explicit session boundary."""
    if audio_id is None:
        return
    buffered = take_claim_buffer(session_id)
    if not buffered:
        return
    candidate = _combine_claim_segments(buffered)
    _dispatch_claim_immediately(session_id, audio_id, candidate)


def _process_chunk_bg(session_id: str, audio_path: str) -> None:
    """
    Background thread: transcribe the accumulated audio, deduplicate segments,
    assign speaker labels, and dispatch async claim extraction.
    """
    session = store.get_session(session_id)
    if not session or session["status"] not in {"capturing", "draining"}:
        _cleanup_tmp(audio_path)
        store.update_session(session_id, processing=False)
        return

    # If it's a microphone source and we have accumulated a large blob, extract the last 15 seconds
    # so Whisper doesn't re-process the entire 10+ minute file every time.
    is_stream = session.get("source") == "stream"
    processed_audio_path = audio_path
    microphone_time_offset = 0.0

    if not is_stream:
        # Convert WebM blob to WAV for reliable duration and trimming.
        # WebM blobs from MediaRecorder lack standard container duration headers,
        # so ffprobe returns N/A and _audio_duration_seconds always fails.
        wav_path = audio_path + "_full.wav"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", audio_path, "-ar", "16000", "-ac", "1", wav_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=15,
            )
            # WAV 16kHz mono s16le: each second = 32000 bytes, header = 44 bytes
            wav_size = os.path.getsize(wav_path)
            wav_duration = max(0.0, (wav_size - 44) / 32000.0)
            logger.info("[Live] WAV conversion: size=%d duration=%.2fs", wav_size, wav_duration)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
            logger.warning("[Live] WAV conversion failed for session=%s: %s; using raw blob", session_id, e)
            wav_path = None
            wav_duration = 0.0

        if wav_path and wav_duration > 0:
            # Use processed_end_time to only transcribe the NEW audio window.
            processed_end: float = float(session.get("processed_end_time", 0.0))
            # Start 1 second before the last processed position for overlap.
            trim_start = max(0.0, processed_end - 1.0)
            live_window_seconds = wav_duration - trim_start

            if live_window_seconds > 1.0 and trim_start > 0.5:
                trimmed_path = audio_path + "_trimmed.wav"
                try:
                    subprocess.run(
                        ["ffmpeg", "-y", "-ss", f"{trim_start:.2f}", "-i", wav_path,
                         "-ar", "16000", "-ac", "1", trimmed_path],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=10,
                    )
                    microphone_time_offset = trim_start
                    processed_audio_path = trimmed_path
                    logger.info(
                        "[Live] Trimmed microphone audio: offset=%.2fs window=%.2fs",
                        microphone_time_offset, live_window_seconds,
                    )
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                    logger.warning("[Live] Trim failed; using full WAV")
                    processed_audio_path = wav_path
            else:
                processed_audio_path = wav_path
                logger.info("[Live] Using full WAV (%.2fs, no trim needed)", wav_duration)
        elif wav_path:
            processed_audio_path = wav_path
        # else: processed_audio_path remains audio_path (raw blob fallback)

    new_raw: list[dict] = []
    audio_id: int | None = None

    try:
        # ── Stage 1: Whisper transcription ────────────────────────────────────
        logger.info("[Live] Transcribing chunk for session=%s path=%s", session_id, processed_audio_path)
        try:
            result = whisper_service.transcribe_audio(
                processed_audio_path,
                language=session.get("detected_language"),
                live_mode=True,
            )
        except whisper_service.TranscriptionError as exc:
            logger.warning("[Live] Whisper rejected audio for session=%s: %s", session_id, exc)
            return
        except Exception as exc:
            logger.exception("[Live] Whisper failed for session=%s: %s", session_id, exc)
            return

        all_segments: list[dict] = result.get("segments", [])
        detected_language = result.get("language")
        if detected_language == "ur":
            detected_language = "hi"
        detected_prob = result.get("language_probability", 1.0)
        # Safe stable language guard: only lock if supported (en, hi) with good confidence and real speech
        if detected_language in {"en", "hi"} and not session.get("detected_language"):
            word_count = len(result.get("text", "").split())
            if detected_prob >= 0.75 and word_count >= 3:
                store.update_session(session_id, detected_language=detected_language)
                logger.info(
                    "[Live] Stable Whisper language selected for session=%s: %s (confidence=%.2f, words=%d)",
                    session_id,
                    detected_language,
                    detected_prob,
                    word_count,
                )
            else:
                logger.info(
                    "[Live] Language detected as '%s' (prob=%.2f, words=%d) - keeping dynamic",
                    detected_language,
                    detected_prob,
                    word_count,
                )
        if not all_segments:
            logger.info("[Live] No speech detected in chunk for session=%s", session_id)
            return

        # ── Stage 2: Deduplicate against already-seen transcript segments ─────
        # Re-fetch to get the latest processed_end_time.
        session = store.get_session(session_id)
        if microphone_time_offset:
            for s in all_segments:
                s["start"] += microphone_time_offset
                s["end"] += microphone_time_offset
        processed_end: float = float(session.get("processed_end_time", 0.0))

        # Deduplicate against already-seen transcript segments near the boundary
        prev_segments = session.get("transcript_segments", [])
        new_raw = [
            s for s in all_segments
            if not _is_duplicate_transcript_segment(s, prev_segments)
        ]

        if not new_raw:
            logger.info(
                "[Live] No new segments for session=%s (already processed up to %.2fs)",
                session_id, processed_end,
            )
            # Still advance processed_end_time.
            new_end = max((float(s["end"]) for s in all_segments), default=processed_end)
            if new_end > processed_end:
                store.update_session(session_id, processed_end_time=new_end)
            return

        # ── Stage 3: Heuristic speaker assignment ─────────────────────────────
        labeled_segments = assign_speakers(new_raw)

        # ── Stage 4: Persist transcript and dispatch each segment immediately ─
        store.add_transcript_segments(session_id, labeled_segments)

        new_end = max((float(s["end"]) for s in all_segments), default=processed_end)
        store.update_session(session_id, processed_end_time=max(processed_end, new_end))

        logger.info(
            "[Live] Transcript segments added: %d for session=%s (processed up to %.2fs)",
            len(labeled_segments), session_id, new_end,
        )

        # Group adjacent Whisper segments before claim detection; verification
        # remains independent once a complete thought is dispatched.
        fresh_session = store.get_session(session_id)
        audio_id = fresh_session["audio_id"] if fresh_session else None
        if audio_id is not None:
            _dispatch_grouped_claims(session_id, audio_id, labeled_segments)

    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "[Live] Unexpected error in chunk processing for session=%s: %s", session_id, exc
        )
    finally:
        # ── CRITICAL: Release processing flag HERE — before Ollama runs ───────
        store.update_session(session_id, processing=False)
        _cleanup_tmp(audio_path)
        if processed_audio_path != audio_path:
            _cleanup_tmp(processed_audio_path)
        # Also clean up intermediate full WAV from microphone conversion
        _cleanup_tmp(audio_path + "_full.wav")

        # ── Dispatch any chunk that arrived while we were processing ──────────
        # Because the frontend sends the full accumulated blob every 8 s, the
        # pending chunk always contains MORE audio than the one we just
        # finished.  Dispatch it immediately so we never fall behind.
        pending = pop_pending_chunk(session_id)
        if pending:
            pending_session = store.get_session(session_id)
            if pending_session and pending_session["status"] in {"capturing", "draining"}:
                logger.info(
                    "[Live] Dispatching pending chunk #%s for session=%s",
                    pending["index"], session_id,
                )
                store.update_session(session_id, processing=True, last_chunk_index=pending["index"])
                threading.Thread(
                    target=_process_chunk_bg,
                    args=(session_id, pending["path"]),
                    daemon=True,
                ).start()
            else:
                _cleanup_tmp(pending["path"])
        _cleanup_stream_work_dir(session_id)
        _maybe_complete_session(session_id)

def _cleanup_tmp(path: str) -> None:
    try:
        if path and os.path.isfile(path):
            os.unlink(path)
    except OSError:
        pass


def _cleanup_stream_work_dir(session_id: str) -> None:
    """Remove a finite stream directory only after capture and workers finish."""
    session = store.get_session(session_id)
    if not session or not session.get("capture_done"):
        return
    q = _SESSION_STREAM_QUEUES.get(session_id)
    if q is not None and not q.empty():
        return
    if session.get("processing") or session.get("pending_chunk"):
        return
    work_dir = session.get("capture_work_dir")
    if work_dir:
        shutil.rmtree(work_dir, ignore_errors=True)


# ──────────────────────────────────────────────────────────────────────────────
# Pydantic request models
# ──────────────────────────────────────────────────────────────────────────────


class StartRequest(BaseModel):
    source: str = "microphone"  # "microphone" | "system"

class StreamRequest(BaseModel):
    url: str

class StopRequest(BaseModel):
    session_id: str


# ──────────────────────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────────────────────


@router.post("/start")
def start_live_session(body: StartRequest, db: Session = Depends(get_db)):
    """
    Create a live session in memory and an AudioRecord in the database.
    Returns ``session_id`` (used for all subsequent chunk/poll/stop calls)
    and ``audio_id`` (FK into the AudioRecord table, visible in Audit Log).
    """
    session_id = store.create_session(body.source)

    record = crud.create_audio_record(
        db,
        filename=f"live_{session_id[:8]}_{body.source}",
        file_path=f"live_session_{session_id}",
    )
    crud.update_audio_record_status(db, record.id, status="live")

    store.update_session(session_id, audio_id=record.id)

    logger.info(
        "[Live] Session started: session_id=%s audio_id=%s source=%s",
        session_id, record.id, body.source,
    )
    return {"session_id": session_id, "audio_id": record.id}


def _classify_stream_error(stderr_text: str) -> str:
    """Map ffprobe/ffmpeg stderr to a structured stream error code."""
    text = (stderr_text or "").strip().lower()
    if not text:
        return "STREAM_UNREACHABLE"
    if "403" in text or "forbidden" in text:
        return "HTTP_403"
    if "404" in text or "not found" in text:
        return "HTTP_404"
    if "could not resolve host" in text or "no address associated" in text or "name or service not known" in text or "temporary failure in name resolution" in text:
        return "DNS_ERROR"
    if "ssl" in text or "tls" in text or "certificate" in text or "handshake" in text or "self signed" in text:
        return "TLS_ERROR"
    if "invalid url" in text or "parse error" in text or "malformed" in text:
        return "INVALID_URL"
    if "unsupported" in text or "unknown format" in text or "codec not currently supported" in text or "no audio stream" in text:
        return "UNSUPPORTED_FORMAT"
    return "STREAM_UNREACHABLE"


def _pick_audio_stream_index(probe_data: dict) -> int | None:
    """Choose a usable audio stream from the ffprobe result regardless of underlying format."""
    streams = list(probe_data.get("streams") or [])
    if not streams:
        return None

    audio_candidates = [
        s for s in streams if s.get("codec_type") == "audio" and s.get("index") is not None
    ]
    if audio_candidates:
        audio_candidates.sort(
            key=lambda s: (
                int(s.get("sample_rate") or 0),
                int(s.get("channels") or 0),
                int(s.get("bit_rate") or 0),
            ),
            reverse=True,
        )
        return int(audio_candidates[0]["index"])

    programs = list(probe_data.get("programs") or [])
    for program in programs:
        p_streams = [
            s for s in (program.get("streams") or [])
            if s.get("codec_type") == "audio" and s.get("index") is not None
        ]
        if p_streams:
            p_streams.sort(
                key=lambda s: (
                    int(s.get("sample_rate") or 0),
                    int(s.get("channels") or 0),
                    int(s.get("bit_rate") or 0),
                ),
                reverse=True,
            )
            return int(p_streams[0]["index"])
    return None


def _process_stream_chunk(
    session_id: str,
    chunk_path: str,
    chunk_index: int,
    chunk_stream_start: float,
) -> None:
    """Transcribe one stream audio chunk with exact timestamps and safe language detection."""
    session = store.get_session(session_id)
    if not session or session["status"] not in {"capturing", "draining"}:
        _cleanup_tmp(chunk_path)
        return

    try:
        current_lang = session.get("detected_language")
        result = whisper_service.transcribe_audio(
            chunk_path,
            language=current_lang,
            live_mode=True,
        )
    except whisper_service.TranscriptionError as exc:
        logger.warning("[Live][Stream] Whisper rejected chunk #%d for session=%s: %s", chunk_index, session_id, exc)
        _cleanup_tmp(chunk_path)
        return
    except Exception as exc:
        logger.exception("[Live][Stream] Whisper failed for chunk #%d session=%s: %s", chunk_index, session_id, exc)
        _cleanup_tmp(chunk_path)
        return

    all_segments: list[dict] = result.get("segments", [])
    detected_language = result.get("language")
    if detected_language == "ur":
        detected_language = "hi"
    detected_prob = result.get("language_probability", 1.0)

    # Safe stable language guard: only lock if supported (en, hi) with good confidence and real speech
    if detected_language in {"en", "hi"} and not session.get("detected_language"):
        word_count = len(result.get("text", "").split())
        if detected_prob >= 0.75 and word_count >= 3:
            store.update_session(session_id, detected_language=detected_language)
            logger.info(
                "[Live] Stable Whisper language selected for session=%s: %s (confidence=%.2f, words=%d)",
                session_id,
                detected_language,
                detected_prob,
                word_count,
            )
        else:
            logger.info(
                "[Live] Language detected as '%s' (prob=%.2f, words=%d) on chunk #%d - keeping dynamic",
                detected_language,
                detected_prob,
                word_count,
                chunk_index,
            )

    if not all_segments:
        logger.info("[Live][Stream] No speech detected in chunk #%d for session=%s", chunk_index, session_id)
        _cleanup_tmp(chunk_path)
        return

    # Offset segment timestamps to actual stream timeline
    for s in all_segments:
        s["start"] = round(s["start"] + chunk_stream_start, 2)
        s["end"] = round(s["end"] + chunk_stream_start, 2)

    # Deduplicate against already-seen segments near the boundary
    session = store.get_session(session_id)
    prev_segments = session.get("transcript_segments", []) if session else []
    processed_end: float = float(session.get("processed_end_time", 0.0)) if session else 0.0

    new_raw = [
        s for s in all_segments
        if not _is_duplicate_transcript_segment(s, prev_segments)
    ]

    if not new_raw:
        logger.info(
            "[Live][Stream] All segments in chunk #%d were duplicates for session=%s",
            chunk_index,
            session_id,
        )
        new_end = max((float(s["end"]) for s in all_segments), default=(chunk_index + 1) * 4.0)
        if new_end > processed_end:
            store.update_session(session_id, processed_end_time=new_end)
        _cleanup_tmp(chunk_path)
        return

    labeled_segments = assign_speakers(new_raw)
    store.add_transcript_segments(session_id, labeled_segments)

    new_end = max((float(s["end"]) for s in all_segments), default=(chunk_index + 1) * 4.0)
    store.update_session(session_id, processed_end_time=max(processed_end, new_end), last_chunk_index=chunk_index)

    logger.info(
        "[Live][Stream] Added %d segments from chunk #%d for session=%s (timeline up to %.2fs): %s",
        len(labeled_segments),
        chunk_index,
        session_id,
        new_end,
        " | ".join(f"\"{s.get('text')}\"" for s in labeled_segments),
    )

    fresh_session = store.get_session(session_id)
    audio_id = fresh_session["audio_id"] if fresh_session else None
    if audio_id is not None:
        _dispatch_grouped_claims(session_id, audio_id, labeled_segments)

    _cleanup_tmp(chunk_path)


def _stream_worker_bg(session_id: str, stream_queue: queue.Queue) -> None:
    """Dedicated sequential worker thread consuming stream audio chunks in strict FIFO order."""
    logger.info("[Live][Stream] Sequential worker thread started for session=%s", session_id)
    try:
        while True:
            try:
                item = stream_queue.get(timeout=1.0)
            except queue.Empty:
                session = store.get_session(session_id)
                if not session or session["status"] not in {"capturing", "draining"}:
                    break
                if session.get("capture_done") and stream_queue.empty():
                    break
                continue

            if item is None:
                # Sentinel signaling end of capture
                stream_queue.task_done()
                break

            chunk_index, chunk_path, chunk_stream_start = item
            store.update_session(session_id, processing=True)
            try:
                _process_stream_chunk(session_id, chunk_path, chunk_index, chunk_stream_start)
            finally:
                store.update_session(session_id, processing=False)
                stream_queue.task_done()
    except Exception as exc:
        logger.exception("[Live][Stream] Worker error for session=%s: %s", session_id, exc)
    finally:
        _SESSION_STREAM_QUEUES.pop(session_id, None)
        _cleanup_stream_work_dir(session_id)
        _maybe_complete_session(session_id)
        logger.info("[Live][Stream] Sequential worker thread ended for session=%s", session_id)


def _stream_capture_bg(session_id: str, url: str, audio_index: int = None) -> None:
    """Run FFmpeg to capture a live media stream into raw PCM chunks, dispatching to Whisper."""
    session = store.get_session(session_id)
    if not session:
        return

    work_dir = tempfile.mkdtemp(prefix=f"stream_{session_id}_")
    store.update_session(session_id, capture_work_dir=work_dir, capture_done=False)

    # Initialize sequential FIFO queue and spawn dedicated consumer thread
    stream_queue: queue.Queue = queue.Queue()
    _SESSION_STREAM_QUEUES[session_id] = stream_queue
    threading.Thread(
        target=_stream_worker_bg,
        args=(session_id, stream_queue),
        daemon=True,
    ).start()

    # Launch FFmpeg to download and extract raw PCM from the stream
    cmd = ["ffmpeg", "-y", "-hide_banner", "-fflags", "nobuffer", "-flags", "low_delay"]
    if url.startswith("http://") or url.startswith("https://"):
        cmd.extend(["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"])
    cmd.extend(["-i", url])
    if audio_index is not None:
        cmd.extend(["-map", f"0:{audio_index}"])
    else:
        cmd.extend(["-map", "0:a:0"])  # fallback to first audio track (avoids multi-audio s16le error)
    cmd.extend(["-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1"])

    logger.info("[Live][Stream] Starting FFmpeg: %s", " ".join(cmd))
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    store.update_session(session_id, capture_process=process)

    chunk_index = 0
    # 4 seconds at 16000Hz mono s16le = 128,000 bytes
    CHUNK_BYTES = 128000

    try:
        while True:
            # Check if session is still active
            sess = store.get_session(session_id)
            if not sess or sess["status"] not in {"capturing", "draining"}:
                break

            # Check if FFmpeg died
            if process.poll() is not None:
                if process.returncode != 0:
                    err = process.stderr.read().decode(errors="replace").strip()
                    logger.error("[Live][Stream] FFmpeg failed: %s", err)
                    store.update_session(
                        session_id,
                        status="error",
                        error=f"Stream URL is valid, but FFmpeg could not open the stream. Details: {err}",
                    )
                else:
                    logger.info("[Live][Stream] FFmpeg exited gracefully.")
                break

            # Read raw bytes (blocks until chunk size is reached or EOF)
            raw_bytes = process.stdout.read(CHUNK_BYTES)
            if not raw_bytes:
                # EOF reached, process will exit in the next loop
                time.sleep(0.5)
                continue

            logger.info("[Live][Stream] bytes received: %d", len(raw_bytes))

            # Only process if we have a reasonable amount of audio
            if len(raw_bytes) < 32000:  # less than 1 sec
                continue

            rms = audioop.rms(raw_bytes, 2)
            duration = len(raw_bytes) / 32000.0
            logger.info("[Live][Stream] [AUDIO] chunk #%d bytes: %d, duration: %.2f sec, RMS: %d", chunk_index, len(raw_bytes), duration, rms)

            if rms < 50:
                logger.info("[Live][Stream] Chunk #%d is silent (RMS=%d), skipping.", chunk_index, rms)
                store.update_session(session_id, audio_status="silent")
                _flush_claim_buffer(session_id, sess.get("audio_id"))
                chunk_index += 1
                continue

            chunk_path = os.path.join(work_dir, f"chunk_{chunk_index}.wav")
            try:
                # Write discrete 4.0s slice directly to WAV
                with wave.open(chunk_path, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(16000)
                    wf.writeframes(raw_bytes)

                chunk_stream_start = chunk_index * 4.0

                store.update_session(session_id, audio_status="transcribing")
                stream_queue.put((chunk_index, chunk_path, chunk_stream_start))
                logger.info(
                    "[Live][Stream] Enqueued chunk #%d (offset=%.2fs, qsize=%d)",
                    chunk_index,
                    chunk_stream_start,
                    stream_queue.qsize(),
                )
                chunk_index += 1
            except Exception as e:
                logger.error("[Live][Stream] Failed to write/queue chunk #%d: %s", chunk_index, e)

    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
        store.mark_capture_done(session_id)
        # Signal the sequential worker thread that capture has finished
        if session_id in _SESSION_STREAM_QUEUES:
            _SESSION_STREAM_QUEUES[session_id].put(None)
        logger.info("[Live][Stream] Capture thread ended for session %s", session_id)


def _resolve_stream_url(url: str) -> str:
    """If the stream URL is an HLS master playlist with multiple variants, resolve to the media variant."""
    if not (url.startswith("http://") or url.startswith("https://")):
        return url
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            content = resp.read().decode("utf-8", errors="ignore")
        if "#EXT-X-STREAM-INF" in content:
            lines = [line.strip() for line in content.splitlines() if line.strip()]
            for idx, line in enumerate(lines):
                if line.startswith("#EXT-X-STREAM-INF") and idx + 1 < len(lines):
                    variant = lines[idx + 1]
                    if not variant.startswith("#"):
                        resolved = urllib.parse.urljoin(url, variant)
                        logger.info("[Live][Stream] Resolved HLS master playlist to variant: %s", resolved)
                        return resolved
    except Exception as exc:
        logger.warning("[Live][Stream] Master playlist resolution failed: %s; using original URL", exc)
    return url


@router.post("/stream")
def start_live_stream(body: StreamRequest, db: Session = Depends(get_db)):
    """Start a live session from a public HTTP/HTTPS stream URL."""
    clean_url = (body.url or "").strip()
    parsed = urllib.parse.urlparse(clean_url)
    if not clean_url or parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return JSONResponse(
            status_code=400,
            content={"error_code": "INVALID_URL", "error": "Please enter a valid HTTP/HTTPS stream URL."},
        )

    stream_target_url = _resolve_stream_url(clean_url)

    audio_index = None
    try:
        process = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-probesize", "500000",
                "-analyzeduration", "1000000",
                "-show_streams",
                "-show_programs",
                "-print_format", "json",
                stream_target_url,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
        )
        if process.returncode == 0:
            try:
                data = json.loads(process.stdout or "{}")
                audio_index = _pick_audio_stream_index(data)
            except json.JSONDecodeError:
                pass
        else:
            logger.warning("[Live][Stream] ffprobe exited with code %s; falling back to dynamic audio capture", process.returncode)
    except subprocess.TimeoutExpired:
        logger.warning("[Live][Stream] ffprobe timed out for %s; falling back to dynamic audio capture", clean_url)
    except FileNotFoundError:
        logger.warning("[Live][Stream] ffprobe binary not found; falling back to dynamic audio capture")
    except Exception as exc:
        logger.warning("[Live][Stream] ffprobe failed (%s); falling back to dynamic audio capture", exc)

    session_id = store.create_session("stream")

    record = crud.create_audio_record(
        db,
        filename=f"stream_{session_id[:8]}",
        file_path=clean_url,
    )
    crud.update_audio_record_status(db, record.id, status="live")
    store.update_session(session_id, audio_id=record.id)

    logger.info(
        "[Live] Stream session started: session_id=%s audio_id=%s url=%s target=%s audio_index=%s",
        session_id, record.id, clean_url, stream_target_url, audio_index,
    )

    threading.Thread(
        target=_stream_capture_bg,
        args=(session_id, stream_target_url, audio_index),
        daemon=True,
    ).start()

    return {"session_id": session_id, "audio_id": record.id}


@router.post("/chunk")
async def receive_chunk(
    session_id: str = Form(...),
    chunk_index: int = Form(...),
    audio: UploadFile = File(...),
):
    """
    Receive the FULL accumulated audio blob from the frontend.

    The frontend builds this blob as:
        new Blob([...allChunks], { type: mimeType })
    so the codec init header is always present and Whisper/ffmpeg can decode it.

    If a previous chunk is still being processed, this chunk is stored in the
    session's pending-chunk slot and dispatched as soon as the current chunk
    finishes.  Only the most-recent pending chunk is kept — it always contains
    more audio than older ones because the frontend accumulates the full blob.
    Returns immediately — transcription runs in a background thread.
    """
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Live session not found.")
    if session["status"] != "capturing":
        return {"ok": True, "skipped": True, "reason": "session_not_active"}

    audio_bytes = await audio.read()
    if not audio_bytes or len(audio_bytes) < 512:
        return {"ok": True, "skipped": True, "reason": "empty_or_tiny_chunk"}

    # Detect extension from MIME type / filename so ffmpeg gets the right hint.
    filename = audio.filename or "chunk.webm"
    ext = ".webm"
    if filename.endswith(".ogg") or (audio.content_type and "ogg" in audio.content_type):
        ext = ".ogg"
    elif filename.endswith(".mp4") or (audio.content_type and "mp4" in audio.content_type):
        ext = ".mp4"

    tmp_path = os.path.join(
        tempfile.gettempdir(),
        f"live_{session_id}_{chunk_index}{ext}",
    )
    with open(tmp_path, "wb") as fh:
        fh.write(audio_bytes)

    logger.info("[Live] Processing chunk #%s for session=%s (%d bytes)",
                chunk_index, session_id, len(audio_bytes))

    if session.get("processing"):
        # A Whisper worker is still running.  Park this chunk in the pending
        # slot so it gets dispatched the moment the worker finishes.
        logger.info(
            "[Live] Whisper busy — queuing chunk #%s for session=%s",
            chunk_index, session_id,
        )
        set_pending_chunk(session_id, tmp_path, chunk_index)
        return {"ok": True, "queued": True, "chunk_index": chunk_index, "bytes": len(audio_bytes)}

    store.update_session(session_id, processing=True, last_chunk_index=chunk_index)

    threading.Thread(
        target=_process_chunk_bg,
        args=(session_id, tmp_path),
        daemon=True,
    ).start()

    return {"ok": True, "chunk_index": chunk_index, "bytes": len(audio_bytes)}


@router.get("/{session_id}")
def get_live_session(session_id: str):
    """
    Return the current state of a live session.
    The frontend polls this every 2 s to update the transcript and claims panels.
    """
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Live session not found.")

    claims = []
    for claim in session.get("claims", []) or []:
        if not isinstance(claim, dict):
            logger.warning("[Live][Poll] Ignoring malformed claim record in session=%s: %r", session_id, claim)
            continue
        normalized = dict(claim)
        normalized["claim_id"] = str(normalized.get("claim_id", normalized.get("db_id", "")))
        normalized["sources"] = normalized.get("sources") if isinstance(normalized.get("sources"), list) else []
        if normalized.get("verification_status") in {
            "claim_detected", "pending", "retrieving_evidence",
            "search_completed", "evidence_validated", "verifying",
        }:
            normalized["verdict"] = None
        else:
            normalized["verdict"] = normalize_verdict(normalized.get("verdict")) if normalized.get("verdict") else None
        normalized["claim_text"] = normalized.get("claim_text") or ""
        normalized["status"] = normalized.get("status") or "pending"
        normalized["verification_status"] = normalized.get("verification_status") or "pending"
        claims.append(normalized)

    return {
        "session_id": session_id,
        "status": session["status"],
        "source": session["source"],
        "audio_id": session["audio_id"],
        "error": session.get("error"),
        "capture_active": session.get("capture_active", False),
        "capture_done": session.get("capture_done", False),
        "processing_active": bool(session.get("processing")),
        "draining": session.get("status") == "draining",
        "session_complete": session.get("status") == "complete",
        "pending_claims": sum(
            claim.get("verification_status") not in {"completed", "failed"}
            for claim in claims
        ),
        "pending_verifications": sum(
            claim.get("verification_status") in {
                "retrieving_evidence", "search_completed", "evidence_validated", "verifying",
            }
            for claim in claims
        ),
        "processing": session.get("processing", False),
        "transcript_segments": session["transcript_segments"],
        "claims": claims,
    }


@router.post("/stop")
def stop_live_session(body: StopRequest, db: Session = Depends(get_db)):
    """
    Stop audio capture for a live session and let already accepted work drain.
    The AudioRecord is finalised only when transcription and all claim workers
    have completed.
    """
    session = store.get_session(body.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Live session not found.")

    store.stop_session(body.session_id)
    store.stop_capture_process(body.session_id)

    _flush_claim_buffer(body.session_id, session.get("audio_id"))

    _maybe_complete_session(body.session_id)

    latest = store.get_session(body.session_id) or session
    logger.info("[Live] Capture stopped: session_id=%s audio_id=%s status=%s", body.session_id, latest.get("audio_id"), latest.get("status"))
    return {
        "ok": True,
        "session_id": body.session_id,
        "audio_id": latest.get("audio_id"),
        "status": latest.get("status"),
    }


def _fmt_time(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 60:02d}:{s % 60:02d}"
