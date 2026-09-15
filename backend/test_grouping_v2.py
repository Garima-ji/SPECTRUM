import json
import re

url = "http://localhost:8000/api/live/5feae4fa-74d6-4d3c-be08-3c1aa8e49408"
import urllib.request
data = json.loads(urllib.request.urlopen(url).read())
segments = data.get("transcript_segments", [])

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

def is_trailing_continuation(text: str) -> bool:
    clean = re.sub(r"[^\w\s\u0900-\u097F]", "", text).strip()
    words = clean.split()
    if not words:
        return False
    last = words[-1].casefold()
    return last in _CONTINUATION_WORDS or last in _HINDI_CONTINUATION_WORDS

def group_should_flush_v2(buffered: list[dict], next_seg: dict | None) -> bool:
    if not buffered:
        return False
    text = " ".join(s["text"].strip() for s in buffered).strip()
    words = text.split()
    duration = float(buffered[-1].get("end", 0.0)) - float(buffered[0].get("start", 0.0))

    trailing = is_trailing_continuation(text)

    # VAD gap (pause in speech >= 0.8s)
    if next_seg:
        vad_gap = float(next_seg.get("start", 0.0)) - float(buffered[-1].get("end", 0.0))
        if vad_gap >= 0.8 and not trailing and len(words) >= 5:
            return True

    # Terminal punctuation: flush ONLY IF NOT trailing on a continuation word
    if re.search(r"[.!?\u0964\u0965]$", text):
        if not trailing:
            return True

    # Buffer length limit (don't buffer forever)
    if duration >= 25.0 or len(words) >= 35:
        if not trailing or len(words) >= 40:
            return True

    return False

# Simulate grouping
buffered = []
groups = []

for i, seg in enumerate(segments):
    next_seg = segments[i + 1] if i + 1 < len(segments) else None
    buffered.append(seg)
    if group_should_flush_v2(buffered, next_seg):
        text = " ".join(s["text"].strip() for s in buffered).strip()
        groups.append((len(buffered), text))
        buffered = []

if buffered:
    text = " ".join(s["text"].strip() for s in buffered).strip()
    groups.append((len(buffered), text))

print(f"Total groups formed: {len(groups)} (was 51 individual chops before)\n")
for idx, (count, g) in enumerate(groups):
    print(f"Group {idx+1} ({count} segs): {g}\n")
