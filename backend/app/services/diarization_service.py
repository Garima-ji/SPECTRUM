"""
diarization_service.py
───────────────────────
Lightweight, CPU-only, zero-dependency speaker assignment for Live Mode.

This is a HEURISTIC approximation — NOT guaranteed real speaker identity.
It works by detecting silence gaps between Whisper segments: a gap longer
than SPEAKER_CHANGE_GAP_S is treated as a potential speaker change, and the
speaker label is toggled.

This produces useful "Speaker 1 / Speaker 2" labels for clear two-speaker
conversations (debate, interview, lecture + Q&A) without any additional
model downloads, GPU, or HuggingFace tokens.

For true multi-speaker diarization, a library such as pyannote-audio
(requires GPU + HF token) could replace this module in the future
without touching any other code.
"""

import logging
from typing import List

logger = logging.getLogger("spectrum")

# Gap between segment END and next segment START that triggers a speaker change.
SPEAKER_CHANGE_GAP_S: float = 1.5

# Number of unique speaker labels to cycle through (wraps around at the limit).
MAX_SPEAKERS: int = 6


def assign_speakers(segments: List[dict]) -> List[dict]:
    """
    Accept a list of Whisper-style segment dicts ``{start, end, text}``
    and return the same list with an added ``speaker`` key on each dict.

    The function never modifies the input dicts in-place; it always returns
    new dicts so the caller's data is unaffected.

    Fallback: if any exception occurs the function returns the original
    segments with ``speaker = "Speaker Unknown"`` — it never raises.
    """
    if not segments:
        return segments

    try:
        labeled: List[dict] = []
        current_speaker: int = 1
        prev_end: float | None = None

        for seg in segments:
            seg_copy = dict(seg)  # never mutate the input

            start = float(seg_copy.get("start", 0))
            end = float(seg_copy.get("end", start))

            if prev_end is not None:
                gap = start - prev_end
                if gap > SPEAKER_CHANGE_GAP_S:
                    # Potential speaker change — cycle to next label.
                    current_speaker = (current_speaker % MAX_SPEAKERS) + 1

            seg_copy["speaker"] = f"Speaker {current_speaker}"
            prev_end = end
            labeled.append(seg_copy)

        return labeled

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[Diarization] Speaker assignment failed (%s). Falling back to 'Speaker Unknown'.",
            exc,
        )
        return [{**seg, "speaker": "Speaker Unknown"} for seg in segments]
