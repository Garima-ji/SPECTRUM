import json
import re
from typing import Any

from app.utils.constants import VERDICT_ALIASES


def clean_json_string(text: str) -> str:
    """Remove a Markdown JSON fence when a model adds one despite the prompt."""
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    return match.group(1).strip() if match else text.strip()


def safe_parse_json(text: str, default_val: Any = None) -> Any:
    try:
        return json.loads(clean_json_string(text))
    except (TypeError, json.JSONDecodeError):
        return default_val


def normalize_verdict(value: Any) -> str:
    if not isinstance(value, str):
        return "UNVERIFIABLE"
    return VERDICT_ALIASES.get(value.strip().upper(), "UNVERIFIABLE")


def normalize_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0

    if 1 < confidence <= 100:
        confidence /= 100
    return round(min(max(confidence, 0.0), 1.0), 3)
