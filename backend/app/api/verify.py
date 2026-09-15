import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import crud, models
from app.database.connection import SessionLocal, get_db
from app.services import verification_service
from app.utils.helpers import normalize_verdict

logger = logging.getLogger("spectrum")
router = APIRouter(prefix="/verify", tags=["Verify"])


def run_async_claim_verification(claim_id: int):
    db = SessionLocal()
    try:
        claim = db.query(models.Claim).filter(models.Claim.id == claim_id).first()
        if not claim:
            logger.warning("[Verifier] Claim %s no longer exists.", claim_id)
            return
        crud.update_claim_verification(
            db, claim_id, "UNVERIFIABLE", "No Reliable Update", "Verification is in progress.", None, None, "verifying"
        )
        result = verification_service.verify_single_claim(claim.claim_text)
        crud.update_claim_verification(
            db,
            claim_id,
            result["verdict"],
            result["status"],
            result["reasoning"],
            result["confidence"],
            result.get("confidence_factors"),
            "completed",
        )
        crud.replace_sources(db, claim_id, result["sources"])
    except Exception as exc:
        logger.exception("[Verifier] Manual verification failed for claim_id=%s.", claim_id)
        crud.update_claim_verification(
            db, claim_id, "UNVERIFIABLE", "No Reliable Update", f"Verification failed: {exc}", 0.0, None, "failed"
        )
    finally:
        db.close()


def _claim_response(claim: models.Claim) -> dict:
    return {
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
        "sources": [{"title": source.title, "url": source.url, "snippet": source.snippet} for source in claim.sources],
    }


@router.post("/claim/{claim_id}")
def verify_claim(claim_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    if not db.query(models.Claim).filter(models.Claim.id == claim_id).first():
        raise HTTPException(status_code=404, detail="Claim not found")
    background_tasks.add_task(run_async_claim_verification, claim_id)
    return {"message": "Verification started.", "claim_id": claim_id}


@router.get("/claim/{claim_id}")
def get_claim_status(claim_id: int, db: Session = Depends(get_db)):
    claim = db.query(models.Claim).filter(models.Claim.id == claim_id).first()
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    return _claim_response(claim)
