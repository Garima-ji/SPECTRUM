import logging
import time
from typing import Any, Callable, Dict

from app.services.claim_service import OllamaTimeoutError, load_prompt, query_ollama
from app.services.query_service import generate_search_query
from app.services.ranking_service import rank_evidence
from app.services.retrieval_service import search_evidence, is_trusted_url
from app.services.progress_service import determine_implementation_status
from app.utils.helpers import normalize_confidence, normalize_verdict, safe_parse_json
from app.utils.constants import STATUSES
from app.config import settings
import json
import threading
import re
from urllib.parse import urlparse

logger = logging.getLogger("spectrum")

_QUERY_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "because", "but", "by", "for",
    "from", "has", "have", "he", "her", "in", "into", "is", "it", "of", "on",
    "or", "that", "the", "their", "they", "to", "was", "were", "which", "who",
    "with",
}
_HINDI_QUERY_STOP_WORDS = {
    "और", "का", "के", "की", "में", "से", "पर", "को", "ने", "यह", "इस", "एक",
    "कि", "है", "था", "थे", "तक", "लिए", "द्वारा", "गया", "गई", "गए",
}
_HINDI_ENGLISH_HINTS = {
    "नई दिल्ली": "New Delhi",
    "भारत": "India",
    "राजधानी": "capital",
    "दिल्ली": "Delhi",
    "जनसंख्या": "population",
    "संविधान": "constitution",
    "प्रधानमंत्री": "Prime Minister",
    "राष्ट्रपति": "President",
    "अर्थव्यवस्था": "economy",
    "वृद्धि": "growth",
    "प्रतिशत": "percent",
}


def _live_search_query(claim_text: str) -> str:
    """Build a concise, high-precision search query from the verified claim."""
    cleaned = claim_text.strip()
    while True:
        m = re.match(
            r"^(well|and|but|so|now|in\s+fact|actually|like\s+i\s+said|as\s+we\s+know|look|action|hey|listen|you\s+know)[,\s!]+",
            cleaned,
            re.IGNORECASE,
        )
        if not m:
            break
        cleaned = cleaned[m.end():].strip()

    words = cleaned.split()
    tokens = [w.strip("\"'.,;:!?()[]{}") for w in words]
    tokens = [t for t in tokens if t]

    entities_and_numbers = []
    content_words = []

    for t in tokens:
        tl = t.casefold()
        if tl in _QUERY_STOP_WORDS or tl in _HINDI_QUERY_STOP_WORDS:
            continue
        if re.search(r"\d", t) or re.match(r"^[A-Z]{2,}$", t) or (re.match(r"^[A-Z][a-z]+$", t) and t != tokens[0]):
            entities_and_numbers.append(t)
        else:
            content_words.append(t)

    ordered_tokens = entities_and_numbers + [w for w in content_words if w not in entities_and_numbers]
    selected = ordered_tokens[:8] if ordered_tokens else tokens[:8]
    return " ".join(selected) or cleaned


def _contains_devanagari(text: str) -> bool:
    return bool(re.search(r"[\u0900-\u097F]", text or ""))


def _heuristic_english_query(claim_text: str) -> str | None:
    """Build an English search phrase for Hindi claims without touching Qwen."""
    if not _contains_devanagari(claim_text):
        return None
    parts: list[str] = []
    for hindi, english in sorted(_HINDI_ENGLISH_HINTS.items(), key=lambda item: -len(item[0])):
        if hindi in claim_text:
            parts.append(english)
    parts.extend(re.findall(r"\d+(?:[.,]\d+)?", claim_text))
    deduped = list(dict.fromkeys(parts))
    return " ".join(deduped) if len(deduped) >= 2 else None


# _optional_qwen_search_query removed — too slow on CPU for live mode.
# Hindi claims use _heuristic_english_query + keyword extraction instead.


def _emit_status(on_status: Callable[[str, str], None] | None, stage: str, label: str) -> None:
    if on_status is not None:
        on_status(stage, label)


def _format_evidence(sources: list[dict]) -> str:
    return "\n\n".join(
        f"Source [{index}]\nTitle: {source['title']}\nURL: {source['url']}\nEvidence: {source.get('snippet', '')}"
        for index, source in enumerate(sources, start=1)
    )


def _calculate_source_diversity(sources: list[dict]) -> float:
    """Calculate source diversity (0-100) using unique domain proportion and breadth.
    
    Prevents single-domain echo chambers: 5 URLs from 1 domain score low (~27%),
    while 5 URLs from independent domains score high (100%).
    """
    if not sources:
        return 0.0
    domains = []
    for s in sources:
        url = str(s.get("url") or s.get("source_url") or "")
        parsed = urlparse(url).netloc.lower().replace("www.", "")
        domain = s.get("source_domain") or s.get("domain") or parsed
        if domain:
            domains.append(domain)
    if not domains:
        return 0.0
    unique_domains = set(domains)
    total = len(domains)
    uniqueness_ratio = len(unique_domains) / float(total)
    domain_breadth = min(1.0, len(unique_domains) / 3.0)
    diversity_score = (0.5 * uniqueness_ratio + 0.5 * domain_breadth) * 100.0
    return round(min(100.0, max(0.0, diversity_score)), 1)


def _calculate_source_consistency(
    verdict: str,
    supporting_count: int,
    contradicting_count: int,
    total_sources: int,
) -> float:
    """Calculate evidence consistency across sources (0-100)."""
    if total_sources == 0:
        return 0.0
    s_count = max(0, int(supporting_count))
    c_count = max(0, int(contradicting_count))
    classified = s_count + c_count

    if verdict == "SUPPORTED":
        consistency = 100.0 - (c_count * 25.0)
        if s_count == 0 and classified > 0:
            consistency = 20.0
    elif verdict == "REFUTED":
        consistency = 100.0 - (s_count * 25.0)
        if c_count == 0 and classified > 0:
            consistency = 20.0
    elif verdict == "CONFLICTING":
        if classified > 0:
            majority = max(s_count, c_count)
            ratio = majority / float(classified)
            consistency = 30.0 + (ratio * 25.0)
        else:
            consistency = 40.0
    else:  # INSUFFICIENT EVIDENCE
        consistency = 50.0 if total_sources > 0 else 0.0

    return round(min(100.0, max(0.0, consistency)), 1)


def _calculate_source_quality(sources: list[dict]) -> float:
    """Calculate source credibility and authority (0-100)."""
    if not sources:
        return 0.0
    total_sources = len(sources)
    trusted_sources = sum(1 for s in sources if is_trusted_url(s.get("url", "") or s.get("source_url", "")))
    volume_score = min(100.0, (total_sources / 4.0) * 100.0)
    trusted_ratio = (trusted_sources / float(max(1, total_sources))) * 100.0
    quality = (0.4 * volume_score) + (0.6 * trusted_ratio)
    return round(min(100.0, max(0.0, quality)), 1)


def _calculate_evidence_strength(
    sources: list[dict],
    claim_text: str,
    evidence_coverage: float,
) -> float:
    """Calculate evidentiary depth and relevance of retrieved sources (0-100).
    
    Decoupled from claim clarity and coverage to prevent double counting.
    Evaluates snippet substantiveness, ranking relevance scores, and entity grounding.
    """
    if not sources:
        return 0.0
    relevance_scores = [float(s.get("relevance_score", 0.6)) for s in sources if s.get("relevance_score") is not None]
    avg_relevance = (sum(relevance_scores) / len(relevance_scores)) if relevance_scores else 0.6
    relevance_pct = min(100.0, avg_relevance * 100.0)
    
    lengths = [len(str(s.get("snippet") or s.get("excerpt") or "")) for s in sources]
    substantive_sources = sum(1 for l in lengths if l >= 80)
    richness_pct = (substantive_sources / float(len(sources))) * 100.0
    
    strength = (0.7 * relevance_pct) + (0.3 * richness_pct)
    if evidence_coverage < 40.0:
        strength = min(strength, evidence_coverage * 1.5)
        
    return round(min(100.0, max(0.0, strength)), 1)


def _make_confidence_factors(
    *,
    final_confidence: float,
    evidence_coverage: float,
    evidence_strength: float,
    source_quality: float,
    source_consistency: float,
    source_diversity: float,
    temporal_relevance: float,
    claim_clarity: float,
    supporting_count: int,
    contradicting_count: int,
    total_source_count: int,
    final_verdict: str,
    llm_confidence: float,
    explanation: str,
) -> dict:
    """Construct full 12-factor explainability dictionary with backwards compatibility."""
    def _clamp(v):
        try:
            return round(min(100.0, max(0.0, float(v))), 1)
        except (ValueError, TypeError):
            return 0.0

    return {
        "final_confidence": _clamp(final_confidence),
        "evidence_coverage": _clamp(evidence_coverage),
        "evidence_strength": _clamp(evidence_strength),
        "source_quality": _clamp(source_quality),
        "source_consistency": _clamp(source_consistency),
        "source_diversity": _clamp(source_diversity),
        "temporal_relevance": _clamp(temporal_relevance),
        "claim_clarity": _clamp(claim_clarity),
        "supporting_count": max(0, int(supporting_count)),
        "contradicting_count": max(0, int(contradicting_count)),
        "total_source_count": max(0, int(total_source_count)),
        "final_verdict": final_verdict,
        "llm_confidence": _clamp(llm_confidence),
        "explanation": explanation,
        # Preserve backwards compatibility keys for existing frontend readers
        "source_count_score": round(min(100.0, (max(0, int(total_source_count)) / 4.0) * 100.0), 1),
        "trusted_ratio_score": _clamp(source_quality),
    }


def verify_single_claim(
    claim_text: str,
    generate_query: bool = True,
    live_mode: bool = False,
    qwen_semaphore: threading.Semaphore | None = None,
    retrieval_timeout_seconds: int = 15,
    qwen_timeout_seconds: int | None = None,
    on_status: Callable[[str, str], None] | None = None,
) -> Dict[str, Any]:
    """Retrieve trusted evidence and ask Ollama to reason only over that evidence."""
    logger.info("[PIPELINE] claim=%s", claim_text)
    logger.info("RETRIEVAL_STARTED\nclaim: %s", claim_text)
    _emit_status(on_status, "retrieving_evidence", "Retrieving Evidence")
    _claim_t0 = time.perf_counter()
    _retrieval_time_s = 0.0
    _qwen_time_s = 0.0

    # ── Step 1: Generate search query ─────────────────────────────────────────
    if generate_query:
        try:
            query = generate_search_query(claim_text)
            logger.info("[PIPELINE] search_query=%s (batch)", query)
        except Exception as exc:
            logger.warning("[PIPELINE] query generation failed, using raw claim: %s", exc)
            query = claim_text
        search_queries = [query]
    else:
        query = _live_search_query(claim_text) if live_mode else claim_text
        search_queries = [query]
        if live_mode and _contains_devanagari(claim_text):
            english_query = ""
            try:
                english_query = generate_search_query(claim_text)
            except Exception:
                english_query = ""
            if not english_query:
                english_query = _heuristic_english_query(claim_text)
            if english_query:
                search_queries = [english_query, query]
                query = english_query
        logger.info("[PIPELINE] search_query=%s", query)
        logger.info("[PIPELINE] search_queries=%s", search_queries)

    # ── Step 2: Retrieve sources ───────────────────────────────────────────────
    retrieval_error: Exception | None = None
    _retrieval_t0 = time.perf_counter()
    try:
        raw_sources = []
        seen_candidate_urls: set[str] = set()
        for search_query in search_queries:
            candidates = search_evidence(
                search_query,
                max_results=5 if live_mode else 8,
                timeout_seconds=retrieval_timeout_seconds,
                live_mode=live_mode,
            )
            unique_candidates = [
                source for source in candidates
                if source.get("url") and not (source.get("url") in seen_candidate_urls or seen_candidate_urls.add(source["url"]))
            ]
            raw_sources.extend(unique_candidates)
        logger.info("[PIPELINE] candidate_count=%d", len(raw_sources))
        logger.info("SEARCH_COMPLETED\ncandidate_count: %d", len(raw_sources))
        _emit_status(on_status, "search_completed", "Search Completed")

        try:
            sources = rank_evidence(
                raw_sources,
                query,
                live_mode=live_mode,
                claim_text=claim_text,
            )
        except TypeError:
            sources = rank_evidence(raw_sources, query)
        for src in sources:
            logger.info(
                "EVIDENCE_ACCEPTED\nurl: %s\ntitle: %s\nexcerpt: %.200s",
                src.get("url", ""), src.get("title", ""), src.get("snippet", ""),
            )
        logger.info(
            "[PIPELINE] accepted_evidence=%s rejected_count=%d",
            [(source.get("title", "")[:80], source.get("url", "")) for source in sources],
            max(0, len(raw_sources) - len(sources)),
        )
        _emit_status(on_status, "evidence_validated", "Evidence Validated")
    except Exception as exc:
        logger.error("[PIPELINE] retrieval failed: %s", exc, exc_info=True)
        sources = []
        retrieval_error = exc
    finally:
        _retrieval_time_s = time.perf_counter() - _retrieval_t0
        logger.info("[TIMING] Evidence retrieval time: %.2fs (claim=%.60s)", _retrieval_time_s, claim_text)

    # ── Step 3: No sources at all — return UNVERIFIABLE with clear distinction ─
    if not sources:
        _total_time_s = time.perf_counter() - _claim_t0
        logger.info(
            "[TIMING] Total claim processing time: %.2fs (retrieval=%.2fs, qwen=%.2fs, claim=%.60s)",
            _total_time_s, _retrieval_time_s, _qwen_time_s, claim_text,
        )
        _timing = {"retrieval_s": round(_retrieval_time_s, 3), "qwen_s": 0.0, "total_s": round(_total_time_s, 3)}
        if retrieval_error is not None:
            return {
                "query": query,
                "sources": [],
                "status": "RETRIEVAL_ERROR",
                "verdict": "UNVERIFIABLE",
                "confidence": 0.0,
                "confidence_factors": json.dumps(_make_confidence_factors(
                    final_confidence=0.0,
                    evidence_coverage=0.0,
                    evidence_strength=0.0,
                    source_quality=0.0,
                    source_consistency=0.0,
                    source_diversity=0.0,
                    temporal_relevance=0.0,
                    claim_clarity=0.0,
                    supporting_count=0,
                    contradicting_count=0,
                    total_source_count=0,
                    final_verdict="UNVERIFIABLE",
                    llm_confidence=0.0,
                    explanation="Low Confidence (0.0%): Evidence retrieval encountered a technical error.",
                )),
                "error_type": "retrieval",
                "reasoning": f"Evidence retrieval failed technically: {retrieval_error}",
                "timing": _timing,
            }
        logger.warning(
            "[VERIFY] final status = INSUFFICIENT EVIDENCE (reason: no sources retrieved — "
            "may be a search failure or genuine absence of evidence)"
        )
        return {
            "query": query,
            "sources": [],
            "status": "Insufficient Evidence",
            "verdict": "INSUFFICIENT EVIDENCE",
            "confidence": 0.0,
            "confidence_factors": json.dumps(_make_confidence_factors(
                final_confidence=0.0,
                evidence_coverage=0.0,
                evidence_strength=0.0,
                source_quality=0.0,
                source_consistency=0.0,
                source_diversity=0.0,
                temporal_relevance=0.0,
                claim_clarity=0.0,
                supporting_count=0,
                contradicting_count=0,
                total_source_count=0,
                final_verdict="INSUFFICIENT EVIDENCE",
                llm_confidence=0.0,
                explanation="Low Confidence (0.0%): No corroborating evidence could be retrieved from search sources.",
            )),
            "reasoning": (
                "No evidence could be retrieved from trusted or general sources for this claim. "
                "This may be due to a search connectivity issue rather than a genuine absence of evidence."
            ),
            "timing": _timing,
        }

    # ── Step 4: Format evidence for Ollama ────────────────────────────────────
    evidence_text = _format_evidence(sources)

    # ── Step 5: Ollama fact-check (also returns implementation_status) ────────
    logger.info("[PIPELINE] qwen_request=started claim=%s evidence_count=%d", claim_text, len(sources))
    logger.info("VERIFICATION_STARTED\nevidence_count: %d", len(sources))
    _emit_status(on_status, "verifying", "Verifying")
    _qwen_t0 = time.perf_counter()
    try:
        prompt = load_prompt("factcheck_prompt.txt").format(claim=claim_text, evidence=evidence_text)
        if qwen_semaphore is not None:
            acquired = qwen_semaphore.acquire(timeout=qwen_timeout_seconds)
            if not acquired:
                raise TimeoutError("No Qwen verification worker became available before timeout.")
        else:
            acquired = False
        try:
            raw_response = query_ollama(
                prompt,
                format_json=True,
                timeout_seconds=qwen_timeout_seconds,
            )
        finally:
            if acquired:
                qwen_semaphore.release()
        _qwen_time_s = time.perf_counter() - _qwen_t0
        logger.info("[TIMING] Qwen verification time: %.2fs (claim=%.60s)", _qwen_time_s, claim_text)
        logger.info("[VERIFY] Ollama raw response = %.2000s", raw_response)
        logger.info("QWEN_RESPONSE\nraw_length: %d", len(raw_response))
    except (TimeoutError, OllamaTimeoutError) as exc:
        _qwen_time_s = time.perf_counter() - _qwen_t0
        _total_time_s = time.perf_counter() - _claim_t0
        logger.error("[VERIFY] Qwen verification timed out: %s", exc)
        logger.info(
            "[TIMING] Total claim processing time: %.2fs (retrieval=%.2fs, qwen=%.2fs, claim=%.60s)",
            _total_time_s, _retrieval_time_s, _qwen_time_s, claim_text,
        )
        return {
            "query": query,
            "sources": sources,
            "status": "VERIFICATION_TIMEOUT",
            "verdict": "INSUFFICIENT EVIDENCE",
            "confidence": 0.0,
            "confidence_factors": json.dumps(_make_confidence_factors(
                final_confidence=0.0,
                evidence_coverage=0.0,
                evidence_strength=0.0,
                source_quality=_calculate_source_quality(sources),
                source_consistency=0.0,
                source_diversity=_calculate_source_diversity(sources),
                temporal_relevance=0.0,
                claim_clarity=0.0,
                supporting_count=0,
                contradicting_count=0,
                total_source_count=len(sources),
                final_verdict="INSUFFICIENT EVIDENCE",
                llm_confidence=0.0,
                explanation="Low Confidence (0.0%): Qwen verification timed out before completing.",
            )),
            "error_type": "verification",
            "reasoning": "Qwen verification timed out after evidence retrieval completed.",
            "timing": {"retrieval_s": round(_retrieval_time_s, 3), "qwen_s": round(_qwen_time_s, 3), "total_s": round(_total_time_s, 3)},
        }
    except Exception as exc:
        _qwen_time_s = time.perf_counter() - _qwen_t0
        _total_time_s = time.perf_counter() - _claim_t0
        logger.error(
            "[VERIFY] Ollama call FAILED (not a verdict failure — this is a technical error): %s",
            exc, exc_info=True,
        )
        logger.info(
            "[TIMING] Total claim processing time: %.2fs (retrieval=%.2fs, qwen=%.2fs, claim=%.60s)",
            _total_time_s, _retrieval_time_s, _qwen_time_s, claim_text,
        )
        return {
            "query": query,
            "sources": sources,
            "status": "No Reliable Update",
            "verdict": "INSUFFICIENT EVIDENCE",
            "confidence": 0.0,
            "confidence_factors": json.dumps(_make_confidence_factors(
                final_confidence=0.0,
                evidence_coverage=0.0,
                evidence_strength=0.0,
                source_quality=_calculate_source_quality(sources),
                source_consistency=0.0,
                source_diversity=_calculate_source_diversity(sources),
                temporal_relevance=0.0,
                claim_clarity=0.0,
                supporting_count=0,
                contradicting_count=0,
                total_source_count=len(sources),
                final_verdict="INSUFFICIENT EVIDENCE",
                llm_confidence=0.0,
                explanation=f"Low Confidence (0.0%): Model processing failed ({exc}).",
            )),
            "error_type": "verification",
            "reasoning": (
                f"Technical failure: Ollama could not be reached or timed out. "
                f"Sources were retrieved but verdict could not be determined. Error: {exc}"
            ),
            "timing": {"retrieval_s": round(_retrieval_time_s, 3), "qwen_s": round(_qwen_time_s, 3), "total_s": round(_total_time_s, 3)},
        }

    # ── Step 7: Parse Ollama response ────────────────────────────────────────
    try:
        payload = safe_parse_json(raw_response)
        if not isinstance(payload, dict):
            raise ValueError(
                f"Expected a JSON object from Ollama, got: {type(payload).__name__}. "
                f"Raw (first 500 chars): {raw_response[:500]}"
            )
    except Exception as exc:
        logger.error("[VERIFY] JSON PARSE ERROR")
        logger.error("[VERIFY] raw Ollama response = %.2000s", raw_response)
        logger.error("[VERIFY] parse exception = %s", exc, exc_info=True)
        _total_time_s = time.perf_counter() - _claim_t0
        logger.info(
            "[TIMING] Total claim processing time: %.2fs (retrieval=%.2fs, qwen=%.2fs, claim=%.60s)",
            _total_time_s, _retrieval_time_s, _qwen_time_s, claim_text,
        )
        return {
            "query": query,
            "sources": sources,
            "status": "No Reliable Update",
            "verdict": "INSUFFICIENT EVIDENCE",
            "confidence": 0.0,
            "confidence_factors": json.dumps(_make_confidence_factors(
                final_confidence=0.0,
                evidence_coverage=0.0,
                evidence_strength=0.0,
                source_quality=_calculate_source_quality(sources),
                source_consistency=0.0,
                source_diversity=_calculate_source_diversity(sources),
                temporal_relevance=0.0,
                claim_clarity=0.0,
                supporting_count=0,
                contradicting_count=0,
                total_source_count=len(sources),
                final_verdict="INSUFFICIENT EVIDENCE",
                llm_confidence=0.0,
                explanation="Low Confidence (0.0%): Ollama response could not be parsed as valid JSON.",
            )),
            "error_type": "verification",
            "reasoning": (
                f"Ollama returned a response that could not be parsed as JSON. "
                f"This is a technical error, not a genuine verdict. "
                f"Raw response preview: {raw_response[:300]}"
            ),
            "timing": {"retrieval_s": round(_retrieval_time_s, 3), "qwen_s": round(_qwen_time_s, 3), "total_s": round(_total_time_s, 3)},
        }

    verdict = normalize_verdict(payload.get("verdict"))
    if verdict not in {"SUPPORTED", "REFUTED", "CONFLICTING", "INSUFFICIENT EVIDENCE"}:
        logger.warning("[VERIFY] Model returned an unsupported verdict=%r", payload.get("verdict"))
        verdict = "INSUFFICIENT EVIDENCE"
    llm_confidence = normalize_confidence(payload.get("confidence"))
    evidence_coverage = payload.get("evidence_coverage", 50)
    claim_clarity = payload.get("claim_clarity", 50)
    temporal_relevance = payload.get("temporal_relevance", 50)
    supporting_count = payload.get("supporting_count", 0)
    contradicting_count = payload.get("contradicting_count", 0)
    reasoning = payload.get("reasoning") or payload.get("explanation") or ""

    # Extract implementation status from the combined response
    raw_impl_status = payload.get("implementation_status", "No Reliable Update")
    implementation_status = raw_impl_status if raw_impl_status in STATUSES else "No Reliable Update"

    if not isinstance(reasoning, str) or not reasoning.strip():
        logger.warning(
            "[VERIFY] Ollama returned no reasoning. Payload keys: %s", list(payload.keys())
        )
        reasoning = "Ollama did not provide an explanation for this verdict."
        
    total_sources = len(sources)
    llm_confidence_val = round(float(llm_confidence) * 100.0 if float(llm_confidence) <= 1.0 else float(llm_confidence), 1)
    ev_coverage_val = round(min(100.0, max(0.0, float(evidence_coverage))), 1)
    claim_clarity_val = round(min(100.0, max(0.0, float(claim_clarity))), 1)
    temporal_rel_val = round(min(100.0, max(0.0, float(temporal_relevance))), 1)
    s_count = int(supporting_count)
    c_count = int(contradicting_count)

    # 1. Source Quality (0-100)
    source_quality = _calculate_source_quality(sources)

    # 2. Source Diversity (0-100)
    source_diversity = _calculate_source_diversity(sources)

    # 3. Source Consistency (0-100)
    source_consistency = _calculate_source_consistency(verdict, s_count, c_count, total_sources)

    # 4. Evidence Strength (0-100, decoupled from clarity/coverage to prevent double-counting)
    evidence_strength = _calculate_evidence_strength(sources, claim_text, ev_coverage_val)

    # 5. Transparent Multi-factor Weighted Confidence (0-100)
    raw_confidence = (
        (evidence_strength * settings.CONFIDENCE_WEIGHT_EVIDENCE_STRENGTH) +
        (source_quality * settings.CONFIDENCE_WEIGHT_SOURCE_QUALITY) +
        (ev_coverage_val * settings.CONFIDENCE_WEIGHT_EVIDENCE_COVERAGE) +
        (source_consistency * settings.CONFIDENCE_WEIGHT_SOURCE_CONSISTENCY) +
        (source_diversity * settings.CONFIDENCE_WEIGHT_SOURCE_DIVERSITY) +
        (temporal_rel_val * settings.CONFIDENCE_WEIGHT_TEMPORAL_RELEVANCE) +
        (claim_clarity_val * settings.CONFIDENCE_WEIGHT_CLAIM_CLARITY)
    )

    # Verdict calibration guards
    if verdict == "INSUFFICIENT EVIDENCE":
        final_confidence = 0.0 if total_sources == 0 else min(raw_confidence * 0.35, 25.0)
    elif verdict == "CONFLICTING":
        final_confidence = min(max(raw_confidence * 0.75, 25.0), 65.0)
    else:
        final_confidence = raw_confidence

    final_confidence = round(min(100.0, max(0.0, final_confidence)), 1)

    # Generate Human-Readable Narrative Explanation
    confidence_tier = "High" if final_confidence >= 75 else ("Moderate" if final_confidence >= 45 else "Low")

    trusted_names = []
    for s in sources:
        domain = s.get("source_domain") or s.get("domain") or ""
        if is_trusted_url(s.get("url", "")) and domain:
            d_clean = domain.replace("www.", "").split(".")[0].upper()
            if d_clean not in trusted_names:
                trusted_names.append(d_clean)

    trusted_mention = f" (including {', '.join(trusted_names[:2])})" if trusted_names else ""

    if verdict == "SUPPORTED":
        explanation = (
            f"{confidence_tier} Confidence ({final_confidence}%): Corroborated by {total_sources} source(s)"
            f"{trusted_mention} with consistent factual reporting."
        )
    elif verdict == "REFUTED":
        explanation = (
            f"{confidence_tier} Confidence ({final_confidence}%): Refuted by authoritative reporting"
            f"{trusted_mention} contradicting the claim."
        )
    elif verdict == "CONFLICTING":
        explanation = (
            f"Moderate Confidence ({final_confidence}%): Sources present conflicting reports "
            f"({s_count} supporting vs {c_count} contradicting)."
        )
    else:  # INSUFFICIENT EVIDENCE
        explanation = (
            f"Low Confidence ({final_confidence}%): Insufficient verifiable evidence found "
            f"in authoritative sources to confirm or refute this statement."
        )

    confidence_factors = _make_confidence_factors(
        final_confidence=final_confidence,
        evidence_coverage=ev_coverage_val,
        evidence_strength=evidence_strength,
        source_quality=source_quality,
        source_consistency=source_consistency,
        source_diversity=source_diversity,
        temporal_relevance=temporal_rel_val,
        claim_clarity=claim_clarity_val,
        supporting_count=s_count,
        contradicting_count=c_count,
        total_source_count=total_sources,
        final_verdict=verdict,
        llm_confidence=llm_confidence_val,
        explanation=explanation,
    )

    _total_time_s = time.perf_counter() - _claim_t0
    logger.info("[PIPELINE] verdict=%s confidence=%.1f", verdict, final_confidence)
    logger.info(
        "[TIMING] Total claim processing time: %.2fs (retrieval=%.2fs, qwen=%.2fs, claim=%.60s)",
        _total_time_s, _retrieval_time_s, _qwen_time_s, claim_text,
    )

    return {
        "query": query,
        "sources": sources,
        "status": implementation_status,
        "verdict": verdict,
        "confidence": final_confidence,
        "confidence_factors": json.dumps(confidence_factors),
        "reasoning": reasoning.strip(),
        "timing": {
            "retrieval_s": round(_retrieval_time_s, 3),
            "qwen_s": round(_qwen_time_s, 3),
            "total_s": round(_total_time_s, 3),
        },
    }
