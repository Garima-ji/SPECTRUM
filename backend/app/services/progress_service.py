import logging

from app.services.claim_service import load_prompt, query_ollama
from app.utils.constants import STATUSES
from app.utils.helpers import safe_parse_json

logger = logging.getLogger("spectrum")


def determine_implementation_status(claim_text: str, evidence_text: str) -> str:
    """Classify implementation status when the evidence supports such a classification."""
    try:
        prompt = load_prompt("progress_prompt.txt").format(claim=claim_text, evidence=evidence_text)
        payload = safe_parse_json(query_ollama(prompt, format_json=True), {})
        status = payload.get("status") if isinstance(payload, dict) else None
        if status in STATUSES:
            logger.info("[Verifier] Implementation status: %s", status)
            return status
    except Exception as exc:
        logger.warning("[Verifier] Implementation-status analysis unavailable: %s", exc)
    return "No Reliable Update"
