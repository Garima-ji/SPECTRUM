import re
import logging
from typing import Dict, List
from urllib.parse import urlparse

import requests

from app.services.retrieval_service import is_trusted_url

logger = logging.getLogger("spectrum")


_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
    "have", "in", "is", "it", "of", "on", "or", "that", "the", "their", "to",
    "was", "were", "with", "this", "they", "he", "she", "about", "after",
}


def _terms(text: str) -> set[str]:
    return {
        token.casefold()
        for token in re.findall(r"[\w\u0900-\u097F]{3,}", text or "")
        if token.casefold() not in _STOP_WORDS
    }


def _years(text: str) -> set[str]:
    return set(re.findall(r"\b(?:19|20)\d{2}\b", text or ""))


def _numbers(text: str) -> set[str]:
    """Keep every stated number; short figures such as 8% are material."""
    return set(re.findall(r"\d+(?:[.,]\d+)?", text or ""))


def _is_article_url(url: str) -> bool:
    """Reject home/search pages even when they belong to a trusted domain."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    path = (parsed.path or "").rstrip("/").casefold()
    if not path:
        return False
    return not any(marker in path for marker in ("/search", "/topic", "/tag", "/category", "/index"))


def _safe_encode_url(url: str) -> str:
    if not isinstance(url, str) or not url:
        return ""
    try:
        from urllib.parse import quote, unquote
        return quote(unquote(url), safe=":/#?=&%")
    except Exception:
        return url


def _url_is_accessible(url: str) -> bool:
    try:
        safe_url = _safe_encode_url(url)
        parsed = urlparse(safe_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False
        response = requests.get(
            safe_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SPECTRUM/1.0"},
            timeout=5,
            allow_redirects=True,
            stream=True,
        )
        status = response.status_code
        response.close()
        return status < 400
    except requests.RequestException:
        return False


def _page_excerpt(url: str, required_terms: set[str]) -> dict | None:
    """Fetch a claim-relevant excerpt and retain the final resolved article URL."""
    try:
        safe_url = _safe_encode_url(url)
        response = requests.get(
            safe_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SPECTRUM/1.0"},
            timeout=5,
            allow_redirects=True,
        )
        content_type = (response.headers.get("content-type") or "").lower()
        if response.status_code >= 400 or "text/html" not in content_type:
            response.close()
            return None

        # Clean HTML before extracting text: strip code blocks and site boilerplate
        html_raw = response.text
        html_clean = re.sub(r"<(script|style|noscript|header|nav|footer)[^>]*>.*?</\1>", " ", html_raw, flags=re.DOTALL | re.IGNORECASE)
        html_clean = re.sub(r"<!--.*?-->", " ", html_clean, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", html_clean)
        # Strip CSS rules and JS fragments
        text = re.sub(r"@media[^{]+\{[^}]*\}", " ", text, flags=re.DOTALL)
        text = re.sub(r"\{[^{}]*(?:display\s*:|font-size\s*:|margin\s*:|padding\s*:|width\s*:)[^{}]*\}", " ", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"\bfunction\s*\w*\s*\([^)]*\)\s*\{[^}]*\}", " ", text, flags=re.DOTALL)
        text = re.sub(r"\b(?:var|let|const|window\.|document\.)\w+\s*=[^;]+;", " ", text)
        text = re.sub(r"\b(Screen Reader Access|Skip to main content|Tamil Version|userway_buttons_wrapper)\b.*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+", " ", text).strip()
        response.close()

        if not text or len(text) < 40:
            return None

        # Discard if what remains is predominantly CSS/JS code
        if re.search(r"(@media|\.userway|\.init|display:\s*none|font-size:\s*\d+%)", text, flags=re.IGNORECASE):
            return None

        lowered = text.casefold()
        positions = [lowered.find(term.casefold()) for term in required_terms if lowered.find(term.casefold()) >= 0] if required_terms else [0]
        if not positions:
            return None
        start = max(0, min(positions) - 120)
        excerpt = text[start:start + 400].strip()

        # Final sanity check on excerpt
        if re.search(r"(@media|\.userway|\.init|display:\s*none)", excerpt, flags=re.IGNORECASE):
            return None

        return {
            "excerpt": excerpt,
            "url": response.url,
        }
    except requests.RequestException:
        return None


def _is_relevant_excerpt(excerpt: str, claim_terms: set[str], query_terms: set[str]) -> bool:
    """Accept a page only when its text demonstrates genuine overlap with core claim terms and is not boilerplate."""
    if not excerpt or len(excerpt) < 30:
        return False
    # Reject code, CSS, or accessibility boilerplate
    if re.search(r"(@media|\.userway|\.init|display:\s*none|font-size:\s*\d+%)", excerpt, flags=re.IGNORECASE):
        return False
    excerpt_terms = _terms(excerpt)
    if not excerpt_terms:
        return False
    combined = claim_terms | query_terms
    if not combined:
        return len(excerpt_terms) >= 3
    overlap = combined & excerpt_terms
    if not overlap:
        return False
    # Require at least one substantial term (>= 4 chars)
    return any(len(term) >= 4 for term in overlap)


def rank_evidence(
    evidence_list: List[Dict[str, str]],
    query: str,
    *,
    live_mode: bool = False,
    claim_text: str = "",
) -> List[Dict[str, str]]:
    """Keep real article pages whose fetched content is claim-relevant."""
    query_terms = _terms(query)
    claim_terms = _terms(claim_text) or query_terms
    query_years = _years(query) | _years(claim_text)
    query_numbers = _numbers(query) | _numbers(claim_text)
    has_devanagari = bool(re.search(r"[\u0900-\u097F]", f"{query} {claim_text}"))
    candidates = []
    seen_urls = set()
    for item in evidence_list:
        url = item.get("url", "")
        if not url or url in seen_urls or not item.get("title") or not _is_article_url(url):
            continue
        if not live_mode and not is_trusted_url(url):
            continue
        seen_urls.add(url)
        evidence_terms = _terms(f"{item.get('title', '')} {item.get('snippet', '')}")
        overlap = (claim_terms | query_terms) & evidence_terms

        # Reject candidates with negligible overlap on longer claims
        min_overlap = 1 if len(claim_terms | query_terms) <= 2 else 2
        if (
            not has_devanagari
            and len(overlap) < min_overlap
            and not any(len(term) >= 8 for term in evidence_terms & (query_terms | claim_terms))
        ):
            logger.info("[Evidence] Rejected low-relevance candidate for query=%s url=%s", query, url)
            continue

        page_evidence = _page_excerpt(url, claim_terms | query_terms)
        if isinstance(page_evidence, dict):
            excerpt = str(page_evidence.get("excerpt") or "").strip()
            resolved_url = str(page_evidence.get("url") or url).strip()
        elif isinstance(page_evidence, str):
            excerpt = page_evidence.strip()
            resolved_url = url
        else:
            excerpt = str(item.get("snippet") or item.get("title") or "").strip()
            resolved_url = url

        if not excerpt or not _is_article_url(resolved_url):
            logger.info("[Evidence] Rejected non-article redirect url=%s", resolved_url)
            continue

        # Discard any excerpt containing raw code / CSS boilerplate
        if re.search(r"(@media|\.userway|\.init|display:\s*none)", excerpt, flags=re.IGNORECASE):
            logger.info("[Evidence] Rejected code/CSS boilerplate url=%s", resolved_url)
            continue

        # Every page (trusted or untrusted) MUST demonstrate claim relevance
        if (
            not _is_relevant_excerpt(excerpt, claim_terms, query_terms)
            and not _is_relevant_excerpt(f"{item.get('title', '')} {item.get('snippet', '')}", claim_terms, query_terms)
        ):
            logger.info("[Evidence] Rejected unrelated page (insufficient term overlap) url=%s", resolved_url)
            continue

        score = len(overlap) / max(1, len(query_terms | claim_terms)) if (query_terms or claim_terms) else 0.5
        if query_years and query_years.intersection(_years(excerpt)):
            score += 0.2
        if query_numbers and query_numbers.intersection(_numbers(excerpt)):
            score += 0.2
        if is_trusted_url(resolved_url):
            score += 0.25
        entity_terms = {t for t in (query_terms | claim_terms) if len(t) >= 4}
        if entity_terms and len(entity_terms.intersection(evidence_terms)) >= 2:
            score += 0.15
        if has_devanagari and _is_relevant_excerpt(excerpt, claim_terms, query_terms):
            score = max(score, 0.55)
        candidates.append({
            **item,
            "url": resolved_url,
            "snippet": excerpt,
            "source_url": resolved_url,
            "domain": urlparse(resolved_url).hostname or "",
            "source_domain": urlparse(resolved_url).hostname or "",
            "excerpt": excerpt,
            "relation": "context",
            "relevance_score": round(score, 3),
        })

    candidates.sort(key=lambda item: item["relevance_score"], reverse=True)
    max_keep = 4 if not live_mode else 3
    logger.info("[Evidence] retained %d relevant accessible sources for query=%s", len(candidates[:max_keep]), query)
    return candidates[:max_keep]
