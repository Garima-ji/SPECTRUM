from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import crud
from app.database.connection import get_db
from app.utils.helpers import normalize_verdict

router = APIRouter(prefix="/claims", tags=["Claims"])


@router.get("/{audio_id}")
def get_claims(audio_id: int, db: Session = Depends(get_db)):
    if not crud.get_audio_record(db, audio_id):
        raise HTTPException(status_code=404, detail="Audio record not found")

    claims = crud.get_claims_by_audio_id(db, audio_id)
    return {
        "audio_id": audio_id,
        "claims": [
            {
                "id": claim.id,
                "claim_text": claim.claim_text,
                "speaker": claim.speaker,
                "timestamp": claim.timestamp,
                "verdict": normalize_verdict(claim.verdict),
                "confidence": claim.confidence,
                "confidence_factors": claim.confidence_factors,
                "reasoning": claim.reasoning,
                "status": claim.status,
                "verification_status": claim.verification_status,
                "sources": [
                    {"title": source.title, "url": source.url, "snippet": source.snippet}
                    for source in claim.sources
                ],
            }
            for claim in claims
        ],
    }
