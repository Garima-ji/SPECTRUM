from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import models


def create_audio_record(db: Session, filename: str, file_path: str):
    record = models.AudioRecord(filename=filename, file_path=file_path, status="processing")
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def get_audio_record(db: Session, audio_id: int):
    return db.query(models.AudioRecord).filter(models.AudioRecord.id == audio_id).first()


def get_audio_records(db: Session, skip: int = 0, limit: int = 100):
    return (
        db.query(models.AudioRecord)
        .order_by(models.AudioRecord.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def update_audio_record_status(
    db: Session,
    audio_id: int,
    status: str,
    transcript_text: str | None = None,
    duration: float | None = None,
    error_message: str | None = None,
):
    record = get_audio_record(db, audio_id)
    if not record:
        return None

    record.status = status
    if transcript_text is not None:
        record.transcript_text = transcript_text
    if duration is not None:
        record.duration = duration
    if error_message is not None:
        record.error_message = error_message
    db.commit()
    db.refresh(record)
    return record


def create_claim(db: Session, audio_id: int, claim_text: str, speaker: str | None = None, timestamp: str | None = None):
    claim = models.Claim(
        audio_id=audio_id,
        claim_text=claim_text,
        speaker=speaker,
        timestamp=timestamp,
        verdict="UNVERIFIABLE",
        status="No Reliable Update",
        verification_status="pending",
    )
    db.add(claim)
    db.commit()
    db.refresh(claim)
    return claim


def get_claims_by_audio_id(db: Session, audio_id: int):
    return db.query(models.Claim).filter(models.Claim.audio_id == audio_id).order_by(models.Claim.id).all()


def update_claim_verification(
    db: Session,
    claim_id: int,
    verdict: str,
    status: str,
    reasoning: str,
    confidence: float | None,
    confidence_factors: str | None,
    verification_status: str,
):
    claim = db.query(models.Claim).filter(models.Claim.id == claim_id).first()
    if not claim:
        return None

    claim.verdict = verdict
    claim.status = status
    claim.reasoning = reasoning
    claim.confidence = confidence
    claim.confidence_factors = confidence_factors
    claim.verification_status = verification_status
    db.commit()
    db.refresh(claim)
    return claim


def replace_sources(db: Session, claim_id: int, sources: list[dict]):
    db.query(models.Source).filter(models.Source.claim_id == claim_id).delete()
    for source in sources:
        db.add(
            models.Source(
                claim_id=claim_id,
                title=source["title"],
                url=source["url"],
                snippet=source.get("snippet", ""),
            )
        )
    db.commit()


def get_dashboard_stats(db: Session):
    total_audio = db.query(models.AudioRecord).count()
    total_claims = db.query(models.Claim).count()
    verdict_counts = db.query(models.Claim.verdict, func.count(models.Claim.id)).group_by(models.Claim.verdict).all()
    status_counts = db.query(models.Claim.status, func.count(models.Claim.id)).group_by(models.Claim.status).all()
    return {
        "total_audio": total_audio,
        "total_claims": total_claims,
        "verdict_distribution": {verdict: count for verdict, count in verdict_counts},
        "status_distribution": {status: count for status, count in status_counts},
    }
