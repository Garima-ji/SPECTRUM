import logging
import os
import re
import time
from typing import Any

from app.config import settings

logger = logging.getLogger("spectrum")

_model: Any = None
_model_load_time: float | None = None  # Track how long model loading took (first load only)
_URDU_SCRIPT = re.compile(r"[\u0600-\u06FF]")
_HINDI_PROMPT = "यह हिंदी समाचार और भाषण है। इसे केवल देवनागरी लिपि में ही लिखें: भारत, सरकार, प्रधानमंत्री, नई दिल्ली, अर्थव्यवस्था, चुनाव, सुप्रीम कोर्ट, विकास।"

_URDU_TO_DEVANAGARI = {
    "ا": "आ", "آ": "आ", "ب": "ब", "پ": "प", "त": "त", "ٹ": "ट", "ث": "स",
    "ج": "ज", "چ": "च", "ح": "ह", "خ": "ख़", "د": "द", "ڈ": "ड", "ذ": "ज़",
    "ر": "र", "ڑ": "ड़", "ز": "ज़", "ژ": "झ़", "س": "स", "ش": "श", "ص": "स",
    "ض": "ज़", "ط": "त", "ظ": "ज़", "ع": "अ", "غ": "ग़", "ف": "फ़", "ق": "क़",
    "ک": "क", "گ": "ग", "ل": "ल", "م": "म", "ن": "न", "ں": "ं", "و": "व",
    "ہ": "ह", "ۂ": "ह", "ۃ": "त", "ھ": "ह", "ء": "", "ی": "ी", "ئ": "",
    "ے": "े", "۔": "।", "؟": "?", "؍": "/",
}


def _transliterate_urdu_to_devanagari(text: str) -> str:
    """Transliterate Urdu Nastaliq characters to Devanagari."""
    return "".join(_URDU_TO_DEVANAGARI.get(ch, ch) for ch in text)


class TranscriptionError(RuntimeError):
    """Raised when an actual transcription cannot be produced safely."""


def get_whisper_model() -> tuple[Any, float | None]:
    """Return (model, load_time_seconds_or_None_if_cached)."""
    global _model, _model_load_time
    if _model is not None:
        return _model, None  # Already cached — no loading time to report

    try:
        from faster_whisper import WhisperModel

        compute_type = settings.WHISPER_COMPUTE_TYPE if settings.WHISPER_DEVICE == "cpu" else "float16"
        logger.info(
            "[Whisper] Loading model=%s device=%s compute_type=%s",
            settings.WHISPER_MODEL,
            settings.WHISPER_DEVICE,
            compute_type,
        )
        t0 = time.perf_counter()
        # Prefer "small" model if available locally because "small" transcribes Hindi accurately into Devanagari
        try:
            _model = WhisperModel(
                "small",
                device=settings.WHISPER_DEVICE,
                compute_type=compute_type,
                local_files_only=True,
            )
        except Exception:
            try:
                _model = WhisperModel(
                    settings.WHISPER_MODEL,
                    device=settings.WHISPER_DEVICE,
                    compute_type=compute_type,
                    local_files_only=True,
                )
            except Exception:
                _model = WhisperModel(
                    settings.WHISPER_MODEL,
                    device=settings.WHISPER_DEVICE,
                    compute_type=compute_type,
                )
        _model_load_time = time.perf_counter() - t0
        logger.info("[Whisper] Model loaded in %.2fs.", _model_load_time)
        return _model, _model_load_time
    except Exception as exc:
        logger.exception("[Whisper] Model loading failed.")
        raise TranscriptionError("Unable to load the configured Whisper model.") from exc


def _detect_language(model: Any, file_path: str) -> tuple[str, float]:
    """Run a fast language-detection sample (no full transcription pass).
    Returns (language_code, elapsed_seconds).
    """
    t0 = time.perf_counter()
    try:
        lang, prob = model.detect_language(file_path)
        elapsed = time.perf_counter() - t0
        logger.info(
            "[Whisper] Language detection: lang=%s confidence=%.2f elapsed=%.2fs",
            lang, prob, elapsed,
        )
        return lang, elapsed
    except Exception:
        elapsed = time.perf_counter() - t0
        logger.warning("[Whisper] Language detection failed in %.2fs; falling back to auto.", elapsed)
        return "", elapsed


SUPPORTED_LANGUAGES: set[str] = {"en", "hi"}


def _clean_hallucination_repetitions(text: str) -> str:
    """Collapse repetitive word hallucinations (e.g. 'word word word word' -> 'word')."""
    # Collapse 3+ consecutive duplicate words (case-insensitive) using backreference \1
    return re.sub(r"\b(\w+)(?:\s+\1){2,}\b", r"\1", text, flags=re.IGNORECASE).strip()


def _collect_segments(
    model: Any,
    file_path: str,
    beam_size: int,
    lang: str | None,
    prompt: str | None,
    *,
    live_mode: bool = False,
) -> tuple[list[dict], str, str, float]:
    vad_parameters = dict(
        min_silence_duration_ms=300 if live_mode else 500,
        speech_pad_ms=80 if live_mode else 200,
    )
    segments, info = model.transcribe(
        file_path,
        language=lang,
        task=settings.WHISPER_TASK,
        beam_size=beam_size,
        vad_filter=True,
        vad_parameters=vad_parameters,
        condition_on_previous_text=False,
        initial_prompt=prompt,
        temperature=0.0,
        no_speech_threshold=0.6,
        compression_ratio_threshold=2.4,
        repetition_penalty=1.15 if live_mode else 1.2,
    )
    transcript_segments = []
    for segment in segments:
        text = _clean_hallucination_repetitions(segment.text.strip())
        if text:
            transcript_segments.append(
                {
                    "start": round(segment.start, 2),
                    "end": round(segment.end, 2),
                    "text": text,
                }
            )
    raw_detected_lang = str(getattr(info, "language", "") or "").lower()
    raw_detected_prob = float(getattr(info, "language_probability", 0.0) or 0.0)

    # Restrict detected language strictly to supported languages (en, hi).
    # Map Urdu (ur) to Hindi (hi) since Hindustani speech sounds phonetically identical.
    if raw_detected_lang in {"ur", "hi"}:
        detected_language = "hi"
    elif raw_detected_lang not in SUPPORTED_LANGUAGES:
        logger.info(
            "[Whisper] Language detected as '%s' (prob=%.2f) not in %s; defaulting to 'en'",
            raw_detected_lang,
            raw_detected_prob,
            SUPPORTED_LANGUAGES,
        )
        detected_language = "en"
    else:
        detected_language = raw_detected_lang

    full_text = " ".join(item["text"] for item in transcript_segments).strip()
    return transcript_segments, full_text, detected_language, raw_detected_prob


def transcribe_audio(file_path: str, language: str | None = None, *, live_mode: bool = False) -> dict:
    """Transcribe audio, auto-detecting language unless WHISPER_LANGUAGE is set.

    Returns a dict with keys:
        text      - full transcript string
        segments  - list of {start, end, text} dicts with real audio timestamps
        duration  - audio duration in seconds (from last segment end timestamp)
        language  - detected or configured language code
        language_probability - confidence score of language detection
        timing    - dict with model_load_s, detection_s, inference_s, total_s
    """
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"Audio file not found: {file_path}")

    pipeline_start = time.perf_counter()
    configured_lang = language or settings.WHISPER_LANGUAGE or None   # None = auto-detect
    if configured_lang and configured_lang not in SUPPORTED_LANGUAGES:
        logger.warning(
            "[Whisper] Configured language '%s' is not supported (%s); falling back to auto/en",
            configured_lang,
            SUPPORTED_LANGUAGES,
        )
        configured_lang = None

    try:
        # ── Stage 1: Model load (cached after first request) ────────────────
        model_result = get_whisper_model()
        if isinstance(model_result, tuple):
            model, model_load_time = model_result
        else:
            # Keep lightweight test doubles and existing callers compatible.
            model, model_load_time = model_result, None

        # ── Stage 2: Stable language selection ─────────────────────────────
        detect_elapsed = 0.0
        effective_lang = configured_lang
        if effective_lang:
            logger.info("[Whisper] Using stable language=%s (skipping detection).", effective_lang)

        # ── Stage 3: Transcription inference ────────────────────────────────
        logger.info(
            "[Whisper] Starting transcription for %s (language=%s).",
            os.path.basename(file_path),
            effective_lang or "auto",
        )

        # Apply Hindi-specific prompt only when Hindi is the target language.
        prompt = _HINDI_PROMPT if effective_lang == "hi" else None

        # Live windows stay on beam_size=1 for latency; batch uploads keep a
        # wider beam for Hindi names and inflected words.
        beam_size = 1 if live_mode else 5
        t_infer_start = time.perf_counter()
        transcript_segments, full_text, detected_language, detected_prob = _collect_segments(
            model,
            file_path,
            beam_size=beam_size,
            lang=effective_lang,
            prompt=prompt,
            live_mode=live_mode,
        )
        effective_lang = effective_lang or detected_language or "en"
        inference_time = time.perf_counter() - t_infer_start

        # If Whisper returned Urdu script or detected Urdu, this indicates
        # Hindustani/Hindi speech transcribed in Arabic/Urdu alphabet.
        # Automatically retry forcing Hindi (Devanagari) with the Hindi initial prompt.
        if _URDU_SCRIPT.search(full_text) or detected_language == "ur":
            logger.warning("[Whisper] Urdu script or 'ur' detected in %s; retrying with Hindi Devanagari forced.", os.path.basename(file_path))
            t_retry = time.perf_counter()
            transcript_segments, full_text, detected_language, detected_prob = _collect_segments(
                model,
                file_path,
                beam_size=5,
                lang="hi",
                prompt=_HINDI_PROMPT,
                live_mode=live_mode,
            )
            effective_lang = "hi"
            inference_time += time.perf_counter() - t_retry

        # If after forcing Hindi, full_text still has Urdu script:
        if _URDU_SCRIPT.search(full_text):
            raise TranscriptionError(
                "Whisper returned Urdu-script output without Hindi/English text; transcript was rejected."
            )

        # Reject only if the text is completely empty
        if not full_text:
            raise TranscriptionError("Whisper did not detect any speech in the uploaded audio.")

        # audio_duration = last segment end timestamp (actual audio time, NOT processing time)
        audio_duration = transcript_segments[-1]["end"] if transcript_segments else 0.0
        total_elapsed = time.perf_counter() - pipeline_start

        # Emit the requested structured timing log
        if model_load_time is not None:
            logger.info("[Whisper] Model loaded in %.2fs.", model_load_time)
        logger.info("[Whisper] Audio preprocessing (language detection): %.2fs.", detect_elapsed)
        logger.info("[Whisper] Inference: %.2fs.", inference_time)
        logger.info(
            "[Whisper] Completed transcription: %d segments, audio_duration=%.2fs, inference_time=%.2fs, total=%.2fs.",
            len(transcript_segments),
            audio_duration,
            inference_time,
            total_elapsed,
        )

        return {
            "text": full_text,
            "segments": transcript_segments,
            "duration": audio_duration,
            "language": effective_lang,
            "language_probability": detected_prob,
            "timing": {
                "model_load_s": model_load_time,
                "detection_s": detect_elapsed,
                "inference_s": inference_time,
                "total_s": total_elapsed,
            },
        }
    except TranscriptionError:
        logger.exception("[Whisper] Transcription rejected.")
        raise
    except Exception as exc:
        logger.exception("[Whisper] Transcription failed.")
        raise TranscriptionError("Whisper could not transcribe the uploaded audio.") from exc
