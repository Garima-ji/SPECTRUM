from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def initialize_database():
    """Create tables and add non-destructive columns for existing installations."""
    Base.metadata.create_all(bind=engine)
    existing_columns = {column["name"] for column in inspect(engine).get_columns("audio_records")}
    claim_columns = {column["name"] for column in inspect(engine).get_columns("claims")}
    additions = []
    if "error_message" not in existing_columns:
        additions.append("ALTER TABLE audio_records ADD COLUMN error_message TEXT")
    if "confidence" not in claim_columns:
        additions.append("ALTER TABLE claims ADD COLUMN confidence FLOAT")
    if "confidence_factors" not in claim_columns:
        additions.append("ALTER TABLE claims ADD COLUMN confidence_factors TEXT")
    if "verification_status" not in claim_columns:
        additions.append("ALTER TABLE claims ADD COLUMN verification_status VARCHAR(32) DEFAULT 'pending'")
    if additions:
        with engine.begin() as connection:
            for statement in additions:
                connection.execute(text(statement))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
