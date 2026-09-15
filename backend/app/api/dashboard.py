from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import crud, connection
from app.services import dashboard_service

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

@router.get("/stats")
def get_stats(db: Session = Depends(connection.get_db)):
    summary = dashboard_service.get_dashboard_summary(db)
    return summary

@router.get("/history")
def get_history(skip: int = 0, limit: int = 100, db: Session = Depends(connection.get_db)):
    records = crud.get_audio_records(db, skip=skip, limit=limit)
    
    response_records = []
    for r in records:
        response_records.append({
            "id": r.id,
            "filename": r.filename,
            "status": r.status,
            "duration": r.duration,
            "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
            "claims_count": len(r.claims)
        })
        
    return response_records
