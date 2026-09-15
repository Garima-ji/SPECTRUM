import json
import re

from app.api.live import (
    _group_should_flush,
    _combine_claim_segments,
    _CONTINUATION_WORDS,
    _HINDI_CONTINUATION_WORDS,
)
from app.services.claim_service import is_checkworthy

with open("/app/dump_transcript.py", "r") as f:
    pass

import urllib.request
url = "http://localhost:8000/api/live/5feae4fa-74d6-4d3c-be08-3c1aa8e49408"
data = json.loads(urllib.request.urlopen(url).read())
segments = data.get("transcript_segments", [])

print(f"Simulating segment grouping on {len(segments)} segments...")

buffer = []
for i, seg in enumerate(segments):
    # Check VAD gap
    if buffer and float(seg.get("start", 0.0)) - float(buffer[-1].get("end", 0.0)) >= 0.5:
        candidate = _combine_claim_segments(buffer)
        cw = is_checkworthy(candidate["text"])
        print(f"VAD GAP at seg {i}: flushed buffer ({len(buffer)} segs). Text: {candidate['text']!r} -> is_checkworthy={cw}")
        buffer = []

    buffer.append(seg)
    if _group_should_flush(buffer):
        candidate = _combine_claim_segments(buffer)
        cw = is_checkworthy(candidate["text"])
        print(f"GROUP FLUSH at seg {i}: flushed buffer ({len(buffer)} segs). Text: {candidate['text']!r} -> is_checkworthy={cw}")
        buffer = []

if buffer:
    candidate = _combine_claim_segments(buffer)
    cw = is_checkworthy(candidate["text"])
    print(f"REMAINING BUFFER ({len(buffer)} segs): Text: {candidate['text']!r} -> is_checkworthy={cw}")
