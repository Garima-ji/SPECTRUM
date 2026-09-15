from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import crud
from app.database.connection import get_db

router = APIRouter(prefix="/transcript", tags=["Transcript"])


@router.get("/{audio_id}")
def get_transcript(audio_id: int, db: Session = Depends(get_db)):
    record = crud.get_audio_record(db, audio_id)
    if not record:
        raise HTTPException(status_code=404, detail="Audio record not found")
    return {
        "audio_id": record.id,
        "filename": record.filename,
        "status": record.status,
        "transcript_text": record.transcript_text,
        "duration": record.duration,
        "error_message": record.error_message,
        "created_at": record.created_at.isoformat() + "Z" if record.created_at else None,
    }
