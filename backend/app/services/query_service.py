import logging

from app.services.claim_service import load_prompt, query_ollama
from app.utils.helpers import safe_parse_json

logger = logging.getLogger("spectrum")


def generate_search_query(claim_text: str, timeout_seconds: int | None = None) -> str:
    """Ask Qwen for a focused query, falling back only to the original real claim."""
    try:
        prompt = load_prompt("query_prompt.txt").format(claim=claim_text)
        payload = safe_parse_json(
            query_ollama(prompt, format_json=True, timeout_seconds=timeout_seconds),
            {},
        )
        query = payload.get("query") if isinstance(payload, dict) else None
        if isinstance(query, str) and query.strip():
            logger.info("[Search] Generated query: %s", query.strip())
            return query.strip()
    except Exception as exc:
        logger.warning("[Search] Query generation failed; using the original claim: %s", exc)

    return claim_text
