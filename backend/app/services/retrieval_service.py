import logging
import html
import re
import time
import threading
from typing import Dict, List
from urllib.parse import parse_qs, quote, unquote, urlparse
try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS
import requests

logger = logging.getLogger("spectrum")

# ──────────────────────────────────────────────────────────────────────────────
# Small in-memory evidence cache.
# ──────────────────────────────────────────────────────────────────────────────
# The same claim (or a near-duplicate) is often spoken more than once in a
# live session, and re-verify from the dashboard re-runs the identical query.
# Caching the raw candidate list for a short TTL avoids redundant DuckDuckGo
# round-trips (typically the slowest part of retrieval) without risking stale
# fact-checks — evidence for a given query rarely changes meaningfully within
# a few minutes.
_EVIDENCE_CACHE_TTL_SECONDS = 300
_evidence_cache: dict[str, tuple[float, list[dict]]] = {}
_evidence_cache_lock = threading.Lock()


_WHITESPACE_RE = re.compile(r"\s+")


def _cache_key(query: str, max_results: int, live_mode: bool) -> str:
    normalized = _WHITESPACE_RE.sub(" ", query.strip().casefold())
    return f"{live_mode}:{max_results}:{normalized}"


def _cache_get(key: str) -> list[dict] | None:
    with _evidence_cache_lock:
        entry = _evidence_cache.get(key)
        if not entry:
            return None
        stored_at, cached_results = entry
        if time.monotonic() - stored_at > _EVIDENCE_CACHE_TTL_SECONDS:
            _evidence_cache.pop(key, None)
            return None
        return cached_results


def _cache_set(key: str, results: list[dict]) -> None:
    with _evidence_cache_lock:
        _evidence_cache[key] = (time.monotonic(), results)
        # Bound the cache so a long-running session doesn't accumulate memory.
        if len(_evidence_cache) > 500:
            oldest_key = min(_evidence_cache, key=lambda k: _evidence_cache[k][0])
            _evidence_cache.pop(oldest_key, None)

# ──────────────────────────────────────────────────────────────────────────────
# Trusted source domains.
# ──────────────────────────────────────────────────────────────────────────────
# Tier-1: official government / intergovernmental / major wire services / verified encyclopedic
TRUSTED_DOMAINS = (
    # National government & constitutional authorities (exact domains)
    "pib.gov.in", "eci.gov.in", "mea.gov.in", "censusindia.gov.in", "isro.gov.in",
    "mospi.gov.in", "rbi.org.in", "sci.gov.in", "drdo.gov.in", "mohfw.gov.in", "india.gov.in",
    # Other national governments
    "whitehouse.gov", "state.gov", "gov.uk", "gov.au", "gc.ca",
    # International organisations
    "who.int", "cdc.gov", "worldbank.org", "un.org", "imf.org",
    "unicef.org", "undp.org", "oecd.org", "wto.org", "iaea.org",
    # Wire services / news
    "reuters.com", "apnews.com", "bbc.com", "bbc.co.uk", "bloomberg.com",
    "theguardian.com", "wsj.com", "nytimes.com", "washingtonpost.com", "aljazeera.com",
    # Indian news (established, generally reliable)
    "thehindu.com", "hindustantimes.com", "timesofindia.indiatimes.com",
    "ndtv.com", "indianexpress.com", "indiatoday.in", "thewire.in",
    "aajtak.in", "jagran.com", "bhaskar.com", "amarujala.com", "abplive.com",
    "zeenews.india.com", "livehindustan.com", "navbharattimes.indiatimes.com",
    "moneycontrol.com", "livemint.com", "economictimes.indiatimes.com",
    # Science / encyclopedic / health
    "nasa.gov", "esa.int", "noaa.gov", "usgs.gov",
    "nationalgeographic.com", "nature.com", "sciencedirect.com",
    "britannica.com", "science.org", "thelancet.com",
    # Fact-check / reference
    "snopes.com", "factcheck.org", "politifact.com", "altnews.in", "boomlive.in", "fullfact.org",
    # Wikipedia
    "wikipedia.org", "wikimedia.org",
    # Verified awards & entertainment bodies
    "emmys.com", "oscars.org", "grammy.com", "tonyawards.com", "bafta.org", "variety.com", "hollywoodreporter.com",
)

# When we do targeted site: searches, prefer these high-recall domains.
FALLBACK_SEARCH_DOMAINS = (
    "pib.gov.in", "reuters.com", "bbc.com", "wikipedia.org", "who.int",
)


def is_trusted_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return False
    return any(host == domain or host.endswith(f".{domain}") for domain in TRUSTED_DOMAINS)


def _clean_snippet(text: str) -> str:
    """Strip raw CSS, JavaScript, screen-reader menus, and HTML comments from snippets."""
    if not text:
        return ""
    # Strip HTML comments
    s = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    # Strip CSS rules and style declarations
    s = re.sub(r"@media[^{]+\{[^}]*\}", " ", s, flags=re.DOTALL)
    s = re.sub(r"\{[^{}]*(?:display\s*:|font-size\s*:|margin\s*:|padding\s*:|width\s*:)[^{}]*\}", " ", s, flags=re.DOTALL | re.IGNORECASE)
    # Strip javascript declarations
    s = re.sub(r"\bfunction\s*\w*\s*\([^)]*\)\s*\{[^}]*\}", " ", s, flags=re.DOTALL)
    s = re.sub(r"\b(?:var|let|const|window\.|document\.)\w+\s*=[^;]+;", " ", s)
    # Strip accessibility / site navigation boilerplate
    s = re.sub(r"\b(Screen Reader Access|Skip to main content|Tamil Version|userway_buttons_wrapper)\b.*", "", s, flags=re.IGNORECASE)
    # Normalize whitespace
    s = re.sub(r"\s+", " ", s).strip()
    # Reject if leftover is essentially CSS/JS artifacts
    if re.search(r"(@media|\.userway|\.init|display:\s*none|font-size:\s*\d+%)", s, flags=re.IGNORECASE):
        return ""
    return s


def _to_evidence(result: dict, allow_any: bool = False) -> dict | None:
    url = result.get("href") or result.get("url")
    title = result.get("title")
    raw_snippet = result.get("body") or result.get("snippet") or ""
    if not isinstance(url, str) or not isinstance(title, str) or not url or not title:
        return None
    if not allow_any and not is_trusted_url(url):
        return None
    cleaned_snippet = _clean_snippet(str(raw_snippet))
    return {"title": title.strip(), "url": url, "snippet": cleaned_snippet}


def _search(ddgs: DDGS, query: str, max_results: int, allow_any: bool = False) -> list[dict]:
    evidence = []
    try:
        for result in ddgs.text(query, max_results=max_results):
            item = _to_evidence(result, allow_any=allow_any)
            if item:
                evidence.append(item)
    except Exception as exc:
        logger.warning("[Search] DuckDuckGo query failed for '%s': %s", query, exc)
    if not evidence:
        evidence = _html_search(query, max_results, allow_any=allow_any)
    if not evidence:
        evidence = _wikipedia_search(query, max_results, allow_any=allow_any)
    return evidence


def _unwrap_duckduckgo_url(url: str) -> str:
    """Resolve a DuckDuckGo redirect to the real article URL before ranking."""
    parsed = urlparse(html.unescape(url))
    if parsed.netloc.endswith("duckduckgo.com"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            return unquote(target)
    if url.startswith("//"):
        return f"https:{url}"
    return url


def _html_search(query: str, max_results: int, allow_any: bool = False) -> list[dict]:
    """Small fallback for DDGS provider failures; results remain candidates only."""
    try:
        response = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": "Mozilla/5.0 (compatible; SPECTRUM/1.0)"},
            timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("[Search] DuckDuckGo HTML fallback failed for '%s': %s", query, exc)
        return []

    evidence = []
    seen_urls = set()
    anchors = re.findall(
        r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        response.text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for href, raw_title in anchors:
        url = _unwrap_duckduckgo_url(href)
        title = re.sub(r"<[^>]+>", " ", html.unescape(raw_title))
        title = re.sub(r"\s+", " ", title).strip()
        item = _to_evidence({"url": url, "title": title, "snippet": ""}, allow_any=allow_any)
        if item and item["url"] not in seen_urls:
            evidence.append(item)
            seen_urls.add(item["url"])
        if len(evidence) >= max_results:
            break
    logger.info("[Search] DuckDuckGo HTML fallback returned %d candidate(s) for '%s'", len(evidence), query)
    return evidence


def _wikipedia_search(query: str, max_results: int, allow_any: bool = False) -> list[dict]:
    """Use Wikipedia's public search API only when web-search providers fail.

    Wikipedia is already a trusted last-resort domain in this application. Each
    returned page still must pass the normal article-content relevance checks.
    """
    languages = ["hi", "en"] if re.search(r"[\u0900-\u097F]", query) else ["en"]
    evidence = []
    seen_urls = set()
    for language in languages:
        try:
            response = requests.get(
                f"https://{language}.wikipedia.org/w/api.php",
                params={"action": "query", "list": "search", "srsearch": query, "format": "json", "srlimit": max_results},
                headers={"User-Agent": "SPECTRUM/1.0 evidence-check"},
                timeout=10,
            )
            response.raise_for_status()
            records = response.json().get("query", {}).get("search", [])
        except (requests.RequestException, ValueError) as exc:
            logger.warning("[Search] Wikipedia fallback failed for '%s': %s", query, exc)
            continue
        for record in records:
            title = str(record.get("title") or "").strip()
            if not title:
                continue
            url = f"https://{language}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"
            snippet = re.sub(r"<[^>]+>", " ", str(record.get("snippet") or ""))
            item = _to_evidence({"url": url, "title": title, "snippet": html.unescape(snippet)}, allow_any=allow_any)
            if item and item["url"] not in seen_urls:
                evidence.append(item)
                seen_urls.add(item["url"])
            if len(evidence) >= max_results:
                return evidence
    logger.info("[Search] Wikipedia fallback returned %d candidate(s) for '%s'", len(evidence), query)
    return evidence


def search_evidence(
    query: str,
    max_results: int = 8,
    timeout_seconds: int = 15,
    live_mode: bool = False,
) -> List[Dict[str, str]]:
    """Retrieve evidence from trusted, public and attributable sources.

    Strategy (most-trusted-first):
    1. Run the bare query and several 'site:' targeted queries collecting only
       results from TRUSTED_DOMAINS.
    2. If fewer than 2 trusted sources are found, run an unfiltered fallback
       search so Ollama always receives *some* evidence to reason from.
       Fallback results are included but logged distinctly.
    """
    logger.info("[VERIFY] search started — query: %s", query)

    cache_key = _cache_key(query, max_results, live_mode)
    cached = _cache_get(cache_key)
    if cached is not None:
        logger.info("[VERIFY] evidence cache HIT for query=%s (%d cached source(s))", query, len(cached))
        return cached

    t0 = time.perf_counter()
    try:
        seen_urls: set[str] = set()
        seen_snippets: list[str] = []
        results: list[dict] = []
        
        # Domains to actively avoid even in fallback
        UNRELIABLE_DOMAINS = ["theonion.com", "babylonbee.com", "infowars.com", "breitbart.com", "naturalnews.com"]

        def is_unreliable(url: str) -> bool:
            try:
                host = (urlparse(url).hostname or "").lower()
                return any(domain in host for domain in UNRELIABLE_DOMAINS)
            except Exception:
                return False

        def is_duplicate_snippet(new_snippet: str) -> bool:
            from difflib import SequenceMatcher
            if not new_snippet or len(new_snippet) < 20:
                return False
            for seen in seen_snippets:
                if SequenceMatcher(None, new_snippet, seen).ratio() > 0.8:
                    return True
            return False

        with DDGS(timeout=timeout_seconds) as ddgs:
            # ── Pass 1: Broad search (trusted + untrusted candidates) ─────────
            # In live mode, run a single broad query first so the ranking stage
            # can pick the most relevant results.  Avoid multiple site: queries
            # that trigger DuckDuckGo rate-limiting in rapid succession.
            if live_mode:
                queries = [query, f"{query} site:wikipedia.org"]
            else:
                domains = FALLBACK_SEARCH_DOMAINS
                queries = [query] + [f"{query} site:{domain}" for domain in domains]

            for search_query in queries:
                for item in _search(ddgs, search_query, max_results=4 if live_mode else 3, allow_any=live_mode):
                    if item["url"] not in seen_urls and not is_unreliable(item["url"]) and not is_duplicate_snippet(item["snippet"]):
                        seen_urls.add(item["url"])
                        seen_snippets.append(item["snippet"])
                        results.append(item)
                        if len(results) >= max_results:
                            break
                # Live mode intentionally stops after a small evidence set.
                if len(results) >= (min(max_results, 4) if live_mode else 4):
                    break

            logger.info(
                "[VERIFY] number of sources found (initial pass) = %d", len(results)
            )

            # ── Pass 2: Unfiltered fallback ───────────────────────────────────
            # If we got fewer than 3 results, broaden the search to any
            # non-null DuckDuckGo result so Ollama has something to reason from.
            if len(results) < 3:
                logger.warning(
                    "[Search] Only %d source(s) found; running unfiltered fallback.",
                    len(results),
                )
                for item in _search(ddgs, query, max_results=6, allow_any=True):
                    if item["url"] not in seen_urls and not is_unreliable(item["url"]) and not is_duplicate_snippet(item["snippet"]):
                        seen_urls.add(item["url"])
                        seen_snippets.append(item["snippet"])
                        results.append(item)
                        if len(results) >= max_results:
                            break

                logger.info(
                    "[VERIFY] number of sources found (after fallback) = %d", len(results)
                )

        if results:
            logger.info(
                "[VERIFY] source URLs/titles = %s",
                [(r["title"][:60], r["url"][:80]) for r in results],
            )
        else:
            logger.warning("[VERIFY] No sources found at all for query: %s", query)

        logger.info("[VERIFY] search elapsed=%.2fs (cache MISS) query=%s", time.perf_counter() - t0, query)
        if results:
            _cache_set(cache_key, results)
        return results

    except Exception as exc:
        logger.error("[Search] Evidence retrieval failed: %s", exc, exc_info=True)
        return []
