from sqlalchemy.orm import Session

from app.database import crud
from app.utils.helpers import normalize_verdict


def get_dashboard_summary(db: Session):
    stats = crud.get_dashboard_stats(db)
    normalized_distribution = {}
    for verdict, count in stats["verdict_distribution"].items():
        normalized = normalize_verdict(verdict)
        normalized_distribution[normalized] = normalized_distribution.get(normalized, 0) + count

    verified_claims = sum(count for verdict, count in normalized_distribution.items() if verdict != "UNVERIFIABLE")
    true_claims = normalized_distribution.get("TRUE", 0) + normalized_distribution.get("PARTIALLY TRUE", 0)
    stats["verdict_distribution"] = normalized_distribution
    stats["verified_claims_count"] = verified_claims
    stats["truth_rate"] = round((true_claims / verified_claims) * 100, 2) if verified_claims else 0.0
    return stats
