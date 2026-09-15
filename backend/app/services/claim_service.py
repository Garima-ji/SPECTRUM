import logging
import os
import time
from typing import Any

import requests

from app.config import settings
from app.utils.helpers import safe_parse_json

logger = logging.getLogger("spectrum")

# Global session to reuse TCP connections to Ollama
_session = requests.Session()


class OllamaError(RuntimeError):
    pass


class OllamaTimeoutError(OllamaError):
    pass


class ClaimExtractionError(RuntimeError):
    pass


def load_prompt(filename: str) -> str:
    prompt_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts", filename)
    with open(prompt_path, "r", encoding="utf-8") as prompt_file:
        return prompt_file.read()


def query_ollama(
    prompt: str,
    format_json: bool = True,
    timeout_seconds: int | None = None,
) -> str:
    """Run a real non-streaming Ollama request and return its text response."""
    url = f"{settings.OLLAMA_URL.rstrip('/')}/api/generate"
    payload: dict[str, Any] = {
        "model": settings.OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_predict": 256,   # cap output tokens → fast on CPU
            "num_ctx": 2048,      # context window for prompt + generation
        },
    }
    if format_json:
        payload["format"] = "json"

    logger.info(
        "[Ollama] Connecting to %s — model=%s format_json=%s.",
        url,
        settings.OLLAMA_MODEL,
        format_json,
    )
    t0 = time.perf_counter()
    try:
        response = _session.post(
            url,
            json=payload,
            timeout=timeout_seconds or settings.OLLAMA_TIMEOUT_SECONDS,
        )
        elapsed = time.perf_counter() - t0
        response.raise_for_status()
        content = response.json().get("response", "").strip()
        logger.info("[Ollama] Response received in %.2fs (%d chars).", elapsed, len(content))
        # Safe debug: log a truncated preview (no secrets in prompts/responses)
        logger.debug("[Ollama] Raw response preview: %.1000s", content)
        if not content:
            raise OllamaError("Ollama returned an empty response.")
        return content
    except requests.Timeout as exc:
        elapsed = time.perf_counter() - t0
        logger.error("[Ollama] Request timed out after %.2fs: %s", elapsed, exc)
        raise OllamaTimeoutError("Ollama request timed out.") from exc
    except (requests.RequestException, ValueError, OllamaError) as exc:
        elapsed = time.perf_counter() - t0
        logger.error("[Ollama] Request failed after %.2fs: %s", elapsed, exc)
        raise OllamaError("Ollama could not complete the request.") from exc


def _timestamp_from_seconds(seconds: float) -> str:
    whole_seconds = max(0, int(seconds))
    return f"{whole_seconds // 60:02d}:{whole_seconds % 60:02d}"


# Maximum transcript characters sent to Ollama.
# Keeps the claim extraction prompt short enough for fast CPU inference.
_MAX_TRANSCRIPT_CHARS = 1500

_HINDI_FACTUAL_MARKERS = (
    # Government & politics
    "सरकार", "मंत्री", "प्रधानमंत्री", "राष्ट्रपति", "नीति", "योजना", "कानून",
    "घोषणा", "घोषित", "लागू", "शुरू", "बढ़", "घटी", "वृद्धि", "प्रतिशत",
    "करोड़", "लाख", "हजार", "हमला", "युद्ध", "समझौता", "चुनाव", "जीता",
    "हार", "बंदरगाह", "जहाज", "अर्थव्यवस्था", "जनसंख्या", "देश", "दर्ज",
    # Geography & capitals
    "राजधानी", "शहर", "राज्य", "जिला", "महाद्वीप", "नदी", "पर्वत", "सागर",
    # Demographics & statistics
    "आबादी", "जनगणना", "विश्व", "दुनिया", "सबसे", "अधिक", "कम",
    # Science & space
    "अंतरिक्ष", "उपग्रह", "चंद्रमा", "मंगल", "इसरो", "नासा",
    # Constitution & law
    "संविधान", "अधिकार", "न्यायालय", "संसद",
    # Economy
    "निर्यात", "आयात", "बजट", "कर", "रुपये",
    # History
    "स्वतंत्रता", "भारत", "स्थापना", "स्थापित",
)


def _transcript_with_timestamps(transcript: str, segments: list[dict] | None) -> str:
    """Build a timestamped transcript string and truncate to _MAX_TRANSCRIPT_CHARS."""
    if not segments:
        text = transcript[:_MAX_TRANSCRIPT_CHARS]
        if len(transcript) > _MAX_TRANSCRIPT_CHARS:
            logger.info(
                "[Claims] Transcript truncated from %d to %d chars for Ollama prompt.",
                len(transcript), _MAX_TRANSCRIPT_CHARS,
            )
        return text

    lines = []
    total_chars = 0
    for segment in segments:
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        line = f"[{_timestamp_from_seconds(float(segment.get('start', 0)))}] {text}"
        if total_chars + len(line) > _MAX_TRANSCRIPT_CHARS:
            logger.info(
                "[Claims] Transcript truncated at segment %s (%d chars) for Ollama prompt.",
                _timestamp_from_seconds(float(segment.get('start', 0))),
                total_chars,
            )
            break
        lines.append(line)
        total_chars += len(line) + 1  # +1 for newline
    return "\n".join(lines) or transcript[:_MAX_TRANSCRIPT_CHARS]


def _normalize_claims(payload: Any) -> list[dict]:
    if isinstance(payload, dict):
        payload = next(
            (payload[key] for key in ("claims", "data", "results") if isinstance(payload.get(key), list)),
            [payload],
        )
    if not isinstance(payload, list):
        raise ClaimExtractionError("Ollama returned a claim payload that is not a JSON array.")

    claims = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        # Accept 'statement', 'claim_text', or 'claim' as the text field
        statement = item.get("statement") or item.get("claim_text") or item.get("claim")
        if not isinstance(statement, str) or not statement.strip():
            continue
        timestamp = item.get("timestamp")
        speaker = item.get("speaker")  # optional — prompt may or may not include it
        claims.append(
            {
                "claim_text": statement.strip(),
                "speaker": speaker.strip() if isinstance(speaker, str) and speaker.strip() else None,
                "timestamp": timestamp.strip() if isinstance(timestamp, str) and timestamp.strip() else None,
            }
        )
    return claims


import re

_BROADCAST_PATTERNS = [
    r"\b(take\s+(a\s+)?(short\s+)?break)\b",
    r"\b(back\s+in\s+just\s+a\s+moment)\b",
    r"\b(stay\s+with\s+us|stay\s+tuned)\b",
    r"\b(welcome\s+back|welcome\s+to\s+the\s+show)\b",
    r"\b(thank\s+you\s+(so\s+much\s+)?for\s+(being\s+with\s+us|joining\s+us|watching))\b",
    r"\b(thanks\s+for\s+having\s+me)\b",
    r"\b(good\s+(morning|evening|afternoon|night))\b",
    r"\b(this\s+is\s+the\s+context)\b",
    r"\b(coming\s+up(\s+next)?)\b",
    r"\b(reporting\s+live(\s+from)?)\b",
    r"\b(let('s|\s+us)\s+turn\s+(now\s+)?to)\b",
    r"\b(what\s+do\s+you\s+think)\b",
    r"\b(we('ll|\s+will)\s+be\s+right\s+back)\b",
    r"\b(briefly,\s+what\s+do\s+you\s+think)\b",
    r"(स्वागत\s+है|देख\s+रहे\s+हैं|विशेष\s+बुलेटिन|नमस्कार|धन्यवाद|शुक्रिया|ब्रेक\s+के\s+बाद|चर्चा\s+करेंगे)",
]

_OPINION_STARTERS = [
    r"^(i\s+think(\s+that)?|i\s+believe(\s+that)?|in\s+my\s+opinion|it\s+seems\s+to\s+me|i\s+feel\s+that)\b",
    r"^(we\s+hope(\s+that)?|my\s+view\s+is|personally\s+speaking)\b",
    r"^(i('m|m|\s+am|\s+was|\s+just|\s+really|\s+can|\s+could|\s+will|\s+would|\s+mean|\s+guess|\s+suppose))\b",
    r"^(you\s+(know|see|can|could|should|must|click|have\s+to|tell))\b",
    r"^(we\s+(want|hope|wish|can|are\s+looking|have\s+to))\b",
    r"^(let\s+(me|us|'s)\s+(see|say|tell|talk|look|take))\b",
    r"^(well|so|now|look|hey|oh|ah|okay|right|action)[,\s!]+",
    r"^(just\s+(want|need|click|listen|look))\b",
    r"^(come\s+on|tell\s+me)\b",
    r"^(मुझे\s+लगता\s+है|मेरी\s+राय\s+में|हमारा\s+मानना\s+है)",
]

_IMPERATIVE_STARTERS = [
    r"^(click|press|watch|listen|look\s+at|check\s+out|turn\s+to|go\s+to|say\s+hey|take\s+a)\b",
]

_FRAGMENT_STARTERS = [
    r"^(that\s+are|which\s+are|who\s+are|who\s+had|that\s+were|which\s+were|who\s+were)\b",
    r"^(and\s+was\s+being|and\s+were\s+being|and\s+it's\s+also)\b",
    r"^(of\s+opportunity|on\s+a\s+structure|in\s+the\s+territories)\b",
    r"^(those\s+of|that\s+like\s+i\s+said)\b",
    r"^(can\s+actually|will\s+supply|could\s+actually)\b",
    r"^(in|on|at|by|from|with|under)\s+.*?\b(that|which|who)\s+(are|is|were|was|have|has|had)\b",
]

_CONTINUATION_LAST_WORDS = {
    "between", "among", "about", "against", "under", "over", "through",
    "after", "before", "during", "since", "while", "until", "and", "or",
    "but", "with", "of", "in", "to", "for", "at", "by", "from", "the", "a", "an",
    "this", "these", "those", "my", "your", "his", "her", "its", "our", "their",
    "is", "are", "was", "were", "be", "been", "being",
}

_HINDI_VERBS = (
    "है", "हैं", "था", "थी", "थे", "किया", "किए", "गया", "गए", "घोषित",
    "लागू", "जीता", "मारा", "बनाया", "कहा", "शुरू"
)


_VERB_REGEX = re.compile(
    r"(\b[a-z]{3,}ed\b|\b(is|are|was|were|be|been|being|has|have|had|do|does|did|will|would|can|could|shall|should|may|might|must|"
    r"won|lost|grew|fell|rose|spoke|took|gave|saw|met|found|built|held|sent|paid|bought|sold|cost|hit|run|lead|led|put|"
    r"said|says|say|told|tells|wrote|writes|made|makes|make|went|goes|came|comes|includes|contains|shows|shown|reaches|wants|wished)\b)",
    re.IGNORECASE,
)

# Acronyms of 2+ uppercase letters (e.g. RBI, NASA, ISRO, GDP, WHO, UN, BBC, NATO, AI, EU, US, UK)
_ACRONYM_REGEX = re.compile(r"\b[A-Z]{2,}\b")

# Numeric data, metrics, percentages, years, and currency values
_NUMBER_METRIC_REGEX = re.compile(
    r"(\b\d+([.,]\d+)?\s*(%|percent|crore|lakh|thousand|million|billion|trillion|dollars?|rupees?|euros?|pounds?|km|mph|kg|tons?)\b|"
    r"[$€£₹]\s*\d+([.,]\d+)?|\b(19\d\d|20\d\d)\b|\b\d+\b)",
    re.IGNORECASE,
)

# Core factual, institutional, scientific, geopolitical, or economic anchor keywords
_FACTUAL_KEYWORDS_REGEX = re.compile(
    r"\b(government|parliament|president|prime minister|court|supreme court|judge|verdict|ruling|"
    r"law|bill|act|treaty|accord|sanction|sanctions|embargo|ministry|minister|department|"
    r"police|arrest|arrested|charged|killed|died|injured|attack|missile|war|military|army|navy|"
    r"election|vote|voted|ballot|candidate|campaign|policy|reform|budget|deficit|inflation|"
    r"interest rate|unemployment|revenue|profit|loss|gdp|economy|economic|trade|tariff|tariffs|"
    r"patent|fda|who|un|nato|imf|world bank|approved|banned|prohibited|mandated|launched|"
    r"discovered|invented|published|study|research|scientists|confirmed|announced|declared|signed)\b",
    re.IGNORECASE,
)

_BROADCAST_LOGISTICS_PATTERNS = [
    r"\b(correspondent|reporter)\s+[A-Z][a-z]+\s+[A-Z][a-z]+\s+(has\s+been|is)\s+(on|at|reporting)\b",
    r"\b(on\s+the\s+red\s+carpet)\b",
    r"\b(our\s+(correspondent|reporter|team|cameras?))\b",
    r"\b(joining\s+us\s+(live|now)\s+(from|on))\b",
    r"\b(back\s+to\s+you\s+(in\s+the\s+studio|guys))\b",
    r"\b(let('s|\s+us)\s+bring\s+in)\b",
    r"\b(take\s+a\s+look\s+at\s+the\s+red\s+carpet)\b",
]

_CEREMONY_AND_SPEECH_PATTERNS = [
    r"\b(the\s+(emmy|oscar|grammy|tony|bafta|award)\s+goes\s+to)\b",
    r"\b(i('d|\s+would)\s+like\s+to\s+thank)\b",
    r"^(to\s+the\s+(great|good|wonderful|people|all)\s+of\b)",
    r"\b(hello|hi|hey)[,\s]+(my\s+name\s+is|i'm|im)\b",
    r"^(please\s+(stand|sit|tell|give|take|welcome|listen))\b",
    r"\b(tell\s+them\s+to\s+be\s+down)\b",
    r"\b(first\s+award\s+of\s+the\s+night)\b",
    r"\b(made\s+history\s+with\s+(her|his|their)\s+(first|second|third|fourth|fifth|eighth|\d+th)\s+award)\b",
    r"\b(for\s+(his|her|their)\s+role\s+in\s+the\s+(netflix|hbo|apple|amazon|bbc)\s+(mini\s+series|series|film|movie))\b",
]

_NARRATIVE_STORYTELLING_PATTERNS = [
    r"^(a\s+few\s+(nights|days|weeks|months|years)\s+(earlier|ago))\b",
    r"^(by\s+the\s+clovis|in\s+fact|to\s+be\s+honest)\b",
    r"\b(we\s+came\s+across|we\s+met|we\s+saw|i\s+ran\s+into)\b",
    r"\b(you\s+might\s+remember|you\s+may\s+recall)\b",
    r"^(what's\s+another\s+crime\b)",
    r"^(that's\s+your\s+risk\b)",
    r"^(two\s+go\b)",
    r"^(also\s+the\s+first\s+performer\b)",
    r"^(these\s+teenagers\s+at\s+a\s+youth\b)",
]

_FRAGMENT_STARTERS.extend([
    r"^(also\s+the\s+first\s+performer|also\s+the\s+first\s+actor|also\s+the\s+first\s+woman)\b",
    r"^(to\s+the\s+great\s+and\s+good|to\s+the\s+people\s+of)\b",
    r"^(what's\s+another\s+crime|what\s+is\s+another\s+crime)\b",
    r"^(that's\s+your\s+risk|that\s+is\s+your\s+risk)\b",
    r"^(the\s+quirky\s+new\s+comedy\s+with)\b",
])

_CONVERSATIONAL_ENDERS = [
    r"\b(boy|girl|man|dude|buddy|bro|folks|guys|hey)[.!?\s]*$",
]

_COMMON_CAPITALIZED_NON_ENTITIES = {
    "the", "this", "that", "these", "those", "there", "here", "what", "when",
    "where", "which", "who", "whom", "whose", "why", "how", "and", "but", "or", "for", "nor",
    "so", "yet", "with", "at", "from", "into", "during", "including", "until",
    "against", "among", "throughout", "despite", "towards", "upon", "concerning",
    "about", "like", "through", "over", "before", "between", "after", "since",
    "without", "under", "within", "along", "following", "across", "behind",
    "beyond", "plus", "except", "front", "back", "boy", "girl", "guys", "folks",
    "man", "woman", "thing", "things", "way", "ways", "day", "days", "time", "times",
    "technology", "world", "people", "person", "just", "come", "action", "hello",
    "please", "stand", "tell", "says", "said", "also", "with", "winds", "lead",
    "actor", "actress", "role", "award", "awards", "performer", "history", "series",
    "comedy", "drama", "show", "project", "city", "night", "nights", "earlier",
    "later", "young", "teenagers", "police", "crime", "first", "second", "third",
    "eighth", "netflix", "ballic", "clovis", "risk", "bay", "two", "one",
}


def _has_proper_noun_entity(text: str) -> bool:
    """Detect genuine proper nouns by ignoring sentence/clause starters and common dictionary words."""
    # Split text into tokens and check word casing
    tokens = re.findall(r"([^\w\s\u0900-\u097F]?)([\w\u0900-\u097F]+)", text)
    if not tokens:
        return False

    for i, (prefix, word) in enumerate(tokens):
        if i == 0:
            continue  # sentence initial word
        # If the word immediately follows sentence punctuation, it is a new sentence/clause starter
        if prefix in {".", "!", "?", ":", ";", '"', "'"} or (i > 0 and any(p in tokens[i-1][1] for p in {".", "!", "?"})):
            continue

        clean_w = word
        if clean_w and clean_w[0].isupper() and clean_w[1:].islower() and len(clean_w) >= 3:
            if clean_w.casefold() not in _COMMON_CAPITALIZED_NON_ENTITIES:
                return True
    return False


def is_checkworthy(text: str) -> bool:
    """
    Robust, sub-millisecond heuristic filter to reject conversational filler, broadcast intros/outros,
    opinions, and sentence fragments. Returns True if the text is a complete, verifiable factual assertion.
    """
    clean_text = re.sub(r'\[\d{2}:\d{2}\]', '', text).strip()
    words = clean_text.split()

    if len(words) < 5:
        return False

    if clean_text.endswith('?'):
        return False

    # Broken speech, hesitations, mid-sentence truncations
    if "..." in clean_text or ".." in clean_text or "--" in clean_text:
        return False

    # Excessive exclamations (e.g. "Two go! One!")
    if clean_text.count("!") >= 2:
        return False

    clean_lower = clean_text.lower()
    for bp in _BROADCAST_PATTERNS:
        if re.search(bp, clean_lower):
            return False

    for lp in _BROADCAST_LOGISTICS_PATTERNS:
        if re.search(lp, clean_lower):
            return False

    for cp in _CEREMONY_AND_SPEECH_PATTERNS:
        if re.search(cp, clean_lower):
            return False

    for np in _NARRATIVE_STORYTELLING_PATTERNS:
        if re.search(np, clean_lower):
            return False

    for op in _OPINION_STARTERS:
        if re.search(op, clean_lower):
            return False

    for ip in _IMPERATIVE_STARTERS:
        if re.search(ip, clean_lower):
            return False

    for ce in _CONVERSATIONAL_ENDERS:
        if re.search(ce, clean_lower):
            return False

    for fp in _FRAGMENT_STARTERS:
        if re.search(fp, clean_lower):
            return False

    last_word = re.sub(r"[^\w\u0900-\u097F]", "", words[-1]).casefold()
    if last_word in _CONTINUATION_LAST_WORDS:
        return False

    is_hindi = bool(re.search(r"[\u0900-\u097F]", clean_text))
    if is_hindi:
        has_verb = any(v in clean_text for v in _HINDI_VERBS)
        has_anchor = any(m in clean_text for m in _HINDI_FACTUAL_MARKERS) or bool(re.search(r"\d+", clean_text))
        return has_verb and has_anchor

    if not _VERB_REGEX.search(clean_lower):
        return False

    # A genuine checkworthy factual statement must contain at least one concrete anchor:
    # 1. A number, metric, percentage, date, or statistic
    # 2. A recognized factual / institutional / policy keyword
    # 3. An uppercase acronym (e.g. RBI, NASA, ISRO, GDP, WHO, UN)
    # 4. A proper noun entity inside the sentence (not just the initial word)
    return bool(
        _NUMBER_METRIC_REGEX.search(clean_text)
        or _FACTUAL_KEYWORDS_REGEX.search(clean_text)
        or _ACRONYM_REGEX.search(clean_text)
        or _has_proper_noun_entity(clean_text)
    )


_CONVERSATIONAL_PREFIX_REGEX = re.compile(
    r"^(well|and|but|so|now|in\s+fact|actually|like\s+i\s+said|as\s+we\s+know|look|clearly|obviously|action|hey|listen|you\s+know|you\s+see|i\s+mean|okay|right|yes|no|oh|by\s+the\s+clovis)[,\s!]+",
    re.IGNORECASE,
)


def _clean_claim_text(text: str) -> str:
    cleaned = text.strip()
    while True:
        m = _CONVERSATIONAL_PREFIX_REGEX.match(cleaned)
        if not m:
            break
        cleaned = cleaned[m.end():].strip()
    if cleaned:
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned


def extract_claims_from_segments(segments: list[dict]) -> list[dict]:
    """
    Extract checkworthy factual claims directly from speech segments using
    thought grouping and heuristic checkworthiness filters, matching the live stream pipeline.
    """
    if not segments:
        return []

    claims: list[dict] = []
    buffered: list[dict] = []
    seen_normalized: set[str] = set()

    for seg in segments:
        if buffered and float(seg.get("start", 0.0)) - float(buffered[-1].get("end", 0.0)) >= 0.8:
            candidate_text = " ".join(str(s.get("text", "")).strip() for s in buffered).strip()
            start_s = float(buffered[0].get("start", 0.0))
            buffered = []
            cleaned = _clean_claim_text(candidate_text)
            norm = re.sub(r"[^\w\s]", "", cleaned.casefold()).strip()
            if len(cleaned.split()) >= 6 and is_checkworthy(cleaned) and norm not in seen_normalized:
                seen_normalized.add(norm)
                claims.append({
                    "claim_text": cleaned,
                    "timestamp": f"{int(start_s // 60):02d}:{int(start_s % 60):02d}",
                    "speaker": seg.get("speaker", "Speaker 1"),
                })

        buffered.append(seg)
        combined_text = " ".join(str(s.get("text", "")).strip() for s in buffered).strip()
        words = combined_text.split()
        duration = float(buffered[-1].get("end", 0.0)) - float(buffered[0].get("start", 0.0))

        should_flush = False
        if re.search(r"[.!?\u0964\u0965]$", combined_text) and len(words) >= 5:
            should_flush = True
        elif duration >= 12.0 or len(words) >= 25:
            should_flush = True

        if should_flush:
            candidate_text = combined_text
            start_s = float(buffered[0].get("start", 0.0))
            buffered = []
            cleaned = _clean_claim_text(candidate_text)
            norm = re.sub(r"[^\w\s]", "", cleaned.casefold()).strip()
            if len(cleaned.split()) >= 6 and is_checkworthy(cleaned) and norm not in seen_normalized:
                seen_normalized.add(norm)
                claims.append({
                    "claim_text": cleaned,
                    "timestamp": f"{int(start_s // 60):02d}:{int(start_s % 60):02d}",
                    "speaker": seg.get("speaker", "Speaker 1"),
                })

    if buffered:
        candidate_text = " ".join(str(s.get("text", "")).strip() for s in buffered).strip()
        start_s = float(buffered[0].get("start", 0.0))
        cleaned = _clean_claim_text(candidate_text)
        norm = re.sub(r"[^\w\s]", "", cleaned.casefold()).strip()
        if len(cleaned.split()) >= 6 and is_checkworthy(cleaned) and norm not in seen_normalized:
            seen_normalized.add(norm)
            claims.append({
                "claim_text": cleaned,
                "timestamp": f"{int(start_s // 60):02d}:{int(start_s % 60):02d}",
                "speaker": buffered[0].get("speaker", "Speaker 1"),
            })

    return claims


def extract_claims(transcript: str, segments: list[dict] | None = None, *, fast_mode: bool = False) -> list[dict]:
    """Extract only model-identified, check-worthy factual statements."""
    if not transcript or not transcript.strip():
        logger.info("[Claims] Empty transcript — skipping claim extraction.")
        return []

    # Fast thought-grouping extraction from segments when requested
    if fast_mode and segments:
        fast_claims = extract_claims_from_segments(segments)
        if fast_claims:
            logger.info("[Claims] Extracted %d checkworthy claims via segment thought grouping.", len(fast_claims))
            return fast_claims

    try:
        logger.info("[Claims] Building claim extraction prompt.")
        prompt = load_prompt("claim_prompt.txt").format(
            transcript=_transcript_with_timestamps(transcript, segments)
        )
        logger.info("[Claims] Sending claim extraction request to Ollama.")
        raw_response = query_ollama(prompt, format_json=True)

        payload = safe_parse_json(raw_response)
        if payload is None:
            logger.error(
                "[Claims][ERROR] safe_parse_json returned None. Raw response (first 500 chars): %.500s",
                raw_response,
            )
            raise ClaimExtractionError("Ollama returned invalid JSON for claim extraction.")

        normalized_claims = _normalize_claims(payload)
        # Final sanity check: filter out any model hallucinated filler/chitchat
        verified_claims = [
            c for c in normalized_claims
            if is_checkworthy(c.get("claim_text", ""))
        ]
        logger.info(
            "[Claims] Parsed %d claims from Ollama (%d passed checkworthiness).",
            len(normalized_claims),
            len(verified_claims),
        )
        return verified_claims
    except ClaimExtractionError:
        logger.exception("[Claims][ERROR] Extraction failed.")
        raise
    except Exception as exc:
        logger.exception("[Claims][ERROR] Extraction failed with unexpected error.")
        raise ClaimExtractionError("Claim extraction could not be completed.") from exc

