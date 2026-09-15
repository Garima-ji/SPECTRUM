from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database.connection import Base


def utc_now():
    return datetime.now(timezone.utc)


class AudioRecord(Base):
    __tablename__ = "audio_records"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    duration = Column(Float, nullable=True)
    transcript_text = Column(Text, nullable=True)
    status = Column(String, default="processing", nullable=False)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    claims = relationship("Claim", back_populates="audio_record", cascade="all, delete-orphan")


class Claim(Base):
    __tablename__ = "claims"

    id = Column(Integer, primary_key=True, index=True)
    audio_id = Column(Integer, ForeignKey("audio_records.id", ondelete="CASCADE"), nullable=False)
    claim_text = Column(Text, nullable=False)
    speaker = Column(String, nullable=True)
    timestamp = Column(String, nullable=True)

    verdict = Column(String, default="UNVERIFIABLE", nullable=False)
    confidence = Column(Float, nullable=True)
    confidence_factors = Column(Text, nullable=True)
    reasoning = Column(Text, nullable=True)
    # Existing UI feature: implementation status for promises and projects.
    status = Column(String, default="No Reliable Update", nullable=False)
    verification_status = Column(String, default="pending", nullable=False)

    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    audio_record = relationship("AudioRecord", back_populates="claims")
    sources = relationship("Source", back_populates="claim", cascade="all, delete-orphan")


class Source(Base):
    __tablename__ = "sources"

    id = Column(Integer, primary_key=True, index=True)
    claim_id = Column(Integer, ForeignKey("claims.id", ondelete="CASCADE"), nullable=False)
    title = Column(String, nullable=True)
    url = Column(String, nullable=False)
    snippet = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now)

    claim = relationship("Claim", back_populates="sources")
